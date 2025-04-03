import asyncio
import json
import os
import re
import shutil
import zipfile

import aiofiles
import aiohttp

from utils import _check_rules, calculate_sha1


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
                    print(f"SHA1校验失败: {dest}")
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
                    return
            except aiohttp.ClientResponseError as e:
                if e.status == 429:
                    # 动态调整等待时间
                    wait_time = min(2 ** retry_count, 30)
                    await asyncio.sleep(wait_time)
                    retry_count += 1
                    if retry_count > max_retries:
                        print(f"Max retries reached for {url}, {dest}")
                        raise e
                else:
                    print(f"Error downloading file: {url}, {dest}")
                    raise e

    async def download_log4j2(self, version_info, version):
        if 'logging' in version_info:
            log4j2_url = version_info['logging']['client']['file']['url']
            log4j2_path = f".minecraft/versions/{version}/log4j2.xml"
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
            library_path = f".minecraft/libraries/{artifact['path']}"
            await self.download_file(artifact['url'], library_path, sha1)
            need_extract = need_extract or "natives" in artifact['url']

            if need_extract:
                extract_path = f".minecraft/versions/{version}/{version}-natives"
                os.makedirs(extract_path, exist_ok=True)

                # 将同步解压操作封装到函数中
                def sync_extract():
                    arch_patterns = {
                        "64": re.compile(r"(x86|i686|arm)"),
                        "32": re.compile(r"(x64|arm64)"),
                        "arm64": re.compile(r"(i386|x86_64)")
                    }
                    current_arch_pattern = arch_patterns.get(os_arch)

                    with zipfile.ZipFile(library_path, 'r') as zip_ref:
                        # 生成过滤后的成员列表
                        filtered_members = []
                        for member in zip_ref.namelist():
                            if any([
                                member.startswith("META-INF/"),
                                member.endswith("/"),
                                member.endswith("LICENSE"),
                                current_arch_pattern and current_arch_pattern.search(member)
                            ]):
                                continue
                            filtered_members.append(member)

                        # 处理文件名冲突
                        member_map = {}
                        for member in filtered_members:
                            base_name = os.path.basename(member)
                            clean_name = re.sub(r'(x86|x64|x32|86|64|32|[-_])', '', base_name)
                            clean_name = re.sub(r'\..+$', '', clean_name)
                            if clean_name not in member_map or len(member) > len(member_map[clean_name]):
                                member_map[clean_name] = member

                        # 执行实际解压
                        for member in member_map.values():
                            target_path = os.path.join(extract_path, os.path.basename(member))
                            with zip_ref.open(member) as source, open(target_path, 'wb') as target:
                                shutil.copyfileobj(source, target) # type:ignore

                # 异步执行同步解压操作
                await asyncio.to_thread(sync_extract)

    async def download_libraries(self, version_info, version, os_name, os_arch):
        libraries = version_info.get('libraries', [])
        tasks = [self.download_library(library, os_name, os_arch, version) for library in libraries]
        await asyncio.gather(*tasks)

    async def download_assets(self, version_info):
        asset_index_url = version_info.get('assetIndex', {}).get('url')
        if asset_index_url:
            asset_index_sha1 = version_info.get('assetIndex', {}).get('sha1')
            asset_index_id = asset_index_url.split('/')[-1].split('.')[0]
            asset_index_path = f".minecraft/assets/indexes/{asset_index_id}.json"
            await self.download_file(asset_index_url, asset_index_path, asset_index_sha1)

            async with aiofiles.open(asset_index_path, 'r') as file:
                asset_index = json.loads(await file.read())

            tasks = []
            for asset, info in asset_index.get('objects', {}).items():
                asset_sha1 = info['hash']
                asset_url = f"https://resources.download.minecraft.net/{asset_sha1[:2]}/{asset_sha1}"
                asset_path = f".minecraft/assets/objects/{asset_sha1[:2]}/{asset_sha1}"
                tasks.append(self.download_file(asset_url, asset_path, asset_sha1))
            await asyncio.gather(*tasks)

    async def download_version(self, version_info, version, os_name, os_arch):
        os.makedirs(f".minecraft/versions/{version}/{version}-natives", exist_ok=True)
        core_jar_url = version_info.get('downloads', {}).get('client', {}).get('url')
        core_jar_path = f".minecraft/versions/{version}/{version}.jar"
        core_jar_sha1 = version_info.get('downloads', {}).get('client', {}).get('sha1')
        await asyncio.gather(self.download_libraries(version_info, version, os_name, os_arch),
                             self.download_assets(version_info),
                             self.download_file(core_jar_url, core_jar_path, core_jar_sha1),
                             self.download_log4j2(version_info, version))
