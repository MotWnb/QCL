import asyncio
import json
import os
import aiofiles
from log_manager import logger
from utils import Utils

class IDownloader:
    async def download_file(self, url, dest, sha1=None): pass
    async def download_log4j2(self, version_info, version): pass
    async def download_library(self, library, os_name, os_arch, version): pass
    async def download_libraries(self, version_info, version, os_name, os_arch): pass
    def replace_with_mirror(self, url): pass
    async def download_assets(self, version_info): pass
    async def download_version(self, version_info, version, os_name, os_arch): pass

class DownloadClass(IDownloader):
    def __init__(self, session, config):
        self.session = session
        self.config = config
        self.utils = Utils()

    async def download_file(self, url, dest, sha1=None):
        logger.debug(f"开始下载文件: {url}")
        if os.path.exists(dest):
            if sha1:
                file_sha1 = await self.utils.calculate_sha1(dest)
                if file_sha1 == sha1: return
                else:
                    logger.error(f"SHA1校验失败: {dest}")
                    os.remove(dest)
            else: os.remove(dest)
        retry_count = 0
        max_retries = 5
        while True:
            try:
                async with self.session.get(url) as response:
                    response.raise_for_status()
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    async with aiofiles.open(dest, 'wb') as file:
                        while True:
                            chunk = await response.content.read(1024 * 1024)
                            if not chunk: break
                            await file.write(chunk)
                    if sha1:
                        file_sha1 = await self.utils.calculate_sha1(dest)
                        if file_sha1 != sha1:
                            logger.error(f"SHA1校验失败: {dest},文件SHA1为{file_sha1},正确的为{sha1},下载链接为{url}")
                            retry_count += 1
                    logger.debug(f"文件下载成功: {dest}")
                    return
            except Exception as e:
                if hasattr(e, 'status') and hasattr(e, 'message'):
                    logger.debug(f"下载失败: {url},错误码为{e.status},错误信息为{e.message}")
                else:
                    logger.debug(f"下载失败: {url},错误信息为{e}")
                retry_count += 1
                wait_time = min(1 ** retry_count, 3)
                await asyncio.sleep(wait_time)
                if retry_count > max_retries:
                    logger.error(f"{dest}下载失败，已达到最大重试次数 {max_retries}")
                    raise e

    async def download_log4j2(self, version_info, version):
        if 'logging' in version_info:
            log4j2_url = version_info['logging']['client']['file']['url']
            if self.config['use_mirror']: log4j2_url = log4j2_url.replace("https://resources.download.minecraft.net", self.config['bmclapi_base_url'] + "/assets")
            log4j2_path = os.path.join(self.config['minecraft_base_dir'], 'versions', version, 'log4j2.xml')
            await self.download_file(log4j2_url, log4j2_path)

    async def download_library(self, library, os_name, os_arch, version):
        artifact = library.get('downloads', {}).get('artifact')
        need_extract = False
        library_path = None
        if not self.utils.check_rules(library, os_name): return
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
            library_path = str(os.path.join(self.config['minecraft_base_dir'], 'libraries', artifact['path']))
            library_url = artifact.get('url')
            if self.config['use_mirror']: library_url = library_url.replace("https://libraries.minecraft.net", self.config['bmclapi_base_url'] + "/maven")
            await self.download_file(library_url, library_path, sha1)
            need_extract = need_extract or "natives" in library_url
        if need_extract:
            extract_path = str(os.path.join(self.config['minecraft_base_dir'], 'versions', version, f"{version}-natives"))
            os.makedirs(extract_path, exist_ok=True)
            if library_path is not None:
                logger.debug(f"开始解压文件: {library_path} 到 {extract_path}")
                await asyncio.to_thread(self.utils.sync_extract, library_path, extract_path)
                logger.debug(f"文件解压完成: {library_path} 到 {extract_path}")

    async def download_libraries(self, version_info, version, os_name, os_arch):
        libraries = version_info.get('libraries', [])
        tasks = [self.download_library(library, os_name, os_arch, version) for library in libraries]
        await asyncio.gather(*tasks)

    def replace_with_mirror(self, url):
        if self.config['use_mirror']:
            url = url.replace(self.config['resource_download_base_url'], self.config['bmclapi_base_url'] + '/assets')
            url = url.replace("https://launcher.mojang.com", self.config['bmclapi_base_url'])
        return url

    async def download_assets(self, version_info):
        asset_index_url = version_info.get('assetIndex', {}).get('url')
        asset_index_url = self.replace_with_mirror(asset_index_url)
        if asset_index_url:
            asset_index_sha1 = version_info.get('assetIndex', {}).get('sha1')
            asset_index_id = version_info.get('assets', '')
            asset_index_path = os.path.join(self.config['minecraft_base_dir'], 'assets', 'indexes', f"{asset_index_id}.json")
            await self.download_file(asset_index_url, asset_index_path, asset_index_sha1)
            async with aiofiles.open(asset_index_path, 'r') as file:
                asset_index = json.loads(await file.read())
            tasks = []
            for asset, info in asset_index.get('objects', {}).items():
                asset_sha1 = info['hash']
                asset_url = f"{self.config['resource_download_base_url']}/{asset_sha1[:2]}/{asset_sha1}"
                asset_url = self.replace_with_mirror(asset_url)
                asset_path = os.path.join(self.config['minecraft_base_dir'], 'assets', 'objects', asset_sha1[:2], asset_sha1)
                tasks.append(self.download_file(asset_url, asset_path, asset_sha1))
            await asyncio.gather(*tasks)

    async def download_version(self, version_info, version, os_name, os_arch):
        os.makedirs(os.path.join(self.config['minecraft_base_dir'], 'versions', version, f"{version}-natives"), exist_ok=True)
        core_jar_url = version_info.get('downloads', {}).get('client', {}).get('url')
        core_jar_url = self.replace_with_mirror(core_jar_url)
        core_jar_path = os.path.join(self.config['minecraft_base_dir'], 'versions', version, f"{version}.jar")
        core_jar_sha1 = version_info.get('downloads', {}).get('client', {}).get('sha1')
        await asyncio.gather(
            self.download_libraries(version_info, version, os_name, os_arch),
            self.download_assets(version_info),
            self.download_file(core_jar_url, core_jar_path, core_jar_sha1),
            self.download_log4j2(version_info, version)
        )