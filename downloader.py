import asyncio
import json
import os
import re
import shutil
import zipfile
import logging

import aiofiles
import aiohttp

from utils import _check_rules, calculate_sha1

# 读取配置文件
with open('config.json', 'r') as f:
    config = json.load(f)

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class DownloadClass:
    def __init__(self, session):
        self.session = session

    async def download_file(self, url, dest, sha1=None):
        if os.path.exists(dest):
            if sha1:
                file_sha1 = await calculate_sha1(dest)
                if file_sha1 == sha1:
                    return
                else:
                    logging.error(f"SHA1校验失败: {dest}")
                    os.remove(dest)
            else:
                os.remove(dest)

        retry_count = 0
        max_retries = 5
        while True:
            try:
                async with self.session.get(url) as response:
                    response.raise_for_status()
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    async with aiofiles.open(dest, 'wb') as file:
                        while True:
                            chunk = await response.content.read(8192)
                            if not chunk:
                                break
                            await file.write(chunk)
                    if sha1:
                        file_sha1 = await calculate_sha1(dest)
                        if file_sha1 != sha1:
                            raise ValueError(f"SHA1校验失败: {dest},文件SHA1为{file_sha1},正确的为{sha1}")
                    logging.debug(f"文件下载成功: {dest}")
                    return
            except aiohttp.ClientResponseError as e:
                if e.status == 429:
                    # 动态调整等待时间
                    wait_time = min(2 ** retry_count, 30)
                    logging.warning(f"收到429错误，等待 {wait_time} 秒后重试: {url}")
                    await asyncio.sleep(wait_time)
                    retry_count += 1
                    if retry_count > max_retries:
                        logging.error(f"{dest}下载失败，已达到最大重试次数 {max_retries}")
                        raise e
                else:
                    logging.error(f"下载失败: {url}")
                    raise e

    async def download_log4j2(self, version_info, version):
        if 'logging' in version_info:
            log4j2_url = version_info['logging']['client']['file']['url']
            if config['use_mirror']:
                log4j2_url = log4j2_url.replace("https://resources.download.minecraft.net", config['bmclapi_base_url'] + "/assets")
            log4j2_path = os.path.join(config['minecraft_base_dir'], 'versions', version, 'log4j2.xml')
            await self.download_file(log4j2_url, log4j2_path)

    async def download_library(self, library, os_name, os_arch, version):
        artifact = library.get('downloads', {}).get('artifact')
        need_extract = False

        if not _check_rules(library, os_name):
            return

        classifiers = library.get('downloads', {}).get('classifiers')
        if classifiers:
            natives = library.get("natives")
            if natives and os_name in natives:
                native_classifier = natives[os_name].replace("${arch}", os_arch)
            else:
                native_classifier = f"natives-{os_name}"
            for native, info in classifiers.items():
                if native == native_classifier:
                    artifact = info
                    need_extract = True
                    break

        if artifact:
            sha1 = artifact.get('sha1')
            library_path = os.path.join(config['minecraft_base_dir'], 'libraries', artifact['path'])
            library_url = artifact.get('url')
            if config['use_mirror']:
                library_url = library_url.replace("https://libraries.minecraft.net", config['bmclapi_base_url'] + "/maven")
            await self.download_file(library_url, library_path, sha1)
            need_extract = need_extract or "natives" in library_url

            if need_extract:
                extract_path = os.path.join(config['minecraft_base_dir'], 'versions', version, f"{version}-natives")
                os.makedirs(extract_path, exist_ok=True)

                # 将同步解压操作封装到函数中
                def sync_extract():
                    # 根据目标架构设置过滤模式
                    arch_patterns = {
                        "x64": re.compile(r"(x86|i686|arm|aarch)"),  # 过滤32位/ARM架构文件
                        "x86": re.compile(r"(x64|arm64|aarch64)"),  # 过滤64位/ARM64文件
                        "arm64": re.compile(r"(i386|x86_64|amd64)")  # 过滤x86架构文件
                    }
                    current_arch_pattern = arch_patterns.get(os_arch)

                    with zipfile.ZipFile(library_path, 'r') as zip_ref:
                        # 第一轮过滤：排除不需要的文件
                        filtered_members = []
                        for member in zip_ref.namelist():
                            # 跳过常见非必要文件
                            if any([
                                member.startswith("META-INF/"),  # 签名文件
                                member.endswith("/"),  # 空目录
                                "LICENSE" in member.upper(),  # 许可证文件
                                # 架构过滤：当存在对应架构模式时检查
                                current_arch_pattern and current_arch_pattern.search(member)
                            ]):
                                continue
                            filtered_members.append(member)

                        # 第二轮处理：解决文件名冲突
                        member_map = {}
                        for member in filtered_members:
                            base_name = os.path.basename(member)
                            # 生成简化文件名（去除架构标识和扩展名）
                            clean_name = re.sub(
                                r'-(linux|windows|macos|arm|aarch|64|32|x86|x64|arm64|v\d+)[_.-]?',
                                '',
                                base_name.split('.')[0]
                            )
                            # 保留路径最长的版本（通常为最完整的实现）
                            if clean_name not in member_map or \
                                    len(member.split('/')) > len(member_map[clean_name].split('/')):
                                member_map[clean_name] = member

                        # 执行解压操作
                        for member in member_map.values():
                            target_path = os.path.join(extract_path, os.path.basename(member))

                            # 确保目标目录存在
                            os.makedirs(os.path.dirname(target_path), exist_ok=True)

                            # 二进制模式复制文件
                            with zip_ref.open(member) as source, \
                                    open(target_path, 'wb') as target:
                                shutil.copyfileobj(source, target)  # type:ignore

                            # 保留文件权限（Linux/macOS需要）
                            if os.name != 'nt':
                                file_info = zip_ref.getinfo(member)
                                os.chmod(target_path, file_info.external_attr >> 16)
                logging.debug(f"开始解压文件: {library_path} 到 {extract_path}")
                await asyncio.to_thread(sync_extract)
                logging.debug(f"文件解压完成: {library_path} 到 {extract_path}")

    async def download_libraries(self, version_info, version, os_name, os_arch):
        libraries = version_info.get('libraries', [])
        tasks = [self.download_library(library, os_name, os_arch, version) for library in libraries]
        await asyncio.gather(*tasks)

    async def download_assets(self, version_info):
        asset_index_url = version_info.get('assetIndex', {}).get('url')
        if config['use_mirror']:
            asset_index_url = asset_index_url.replace(config['resource_download_base_url'], config['bmclapi_base_url'] + '/assets')
        if asset_index_url:
            asset_index_sha1 = version_info.get('assetIndex', {}).get('sha1')
            asset_index_id = asset_index_url.split('/')[-1].split('.')[0]
            asset_index_path = os.path.join(config['minecraft_base_dir'], 'assets', 'indexes', f"{asset_index_id}.json")
            await self.download_file(asset_index_url, asset_index_path, asset_index_sha1)

            async with aiofiles.open(asset_index_path, 'r') as file:
                asset_index = json.loads(await file.read())

            tasks = []
            for asset, info in asset_index.get('objects', {}).items():
                asset_sha1 = info['hash']
                asset_url = f"{config['resource_download_base_url']}/{asset_sha1[:2]}/{asset_sha1}"
                if config['use_mirror']:
                    asset_url = asset_url.replace(config['resource_download_base_url'], config['bmclapi_base_url'] + '/assets')
                asset_path = os.path.join(config['minecraft_base_dir'], 'assets', 'objects', asset_sha1[:2], asset_sha1)
                tasks.append(self.download_file(asset_url, asset_path, asset_sha1))
            await asyncio.gather(*tasks)

    async def download_version(self, version_info, version, os_name, os_arch):
        os.makedirs(os.path.join(config['minecraft_base_dir'], 'versions', version, f"{version}-natives"), exist_ok=True)
        core_jar_url = version_info.get('downloads', {}).get('client', {}).get('url')
        core_jar_path = os.path.join(config['minecraft_base_dir'], 'versions', version, f"{version}.jar")
        core_jar_sha1 = version_info.get('downloads', {}).get('client', {}).get('sha1')
        if config['use_mirror']:
            core_jar_url = core_jar_url.replace("https://launcher.mojang.com", config['bmclapi_base_url'])
        await asyncio.gather(self.download_libraries(version_info, version, os_name, os_arch),
                             self.download_assets(version_info),
                             self.download_file(core_jar_url, core_jar_path, core_jar_sha1),
                             self.download_log4j2(version_info, version))
    