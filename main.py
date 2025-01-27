import os
import json
import platform
import shutil
import aiohttp
import aiofiles
import asyncio
import zipfile

class DownloadClass:
    def __init__(self, session):
        self.session = session

    async def download_file(self, url, dest):
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
        except aiohttp.ClientResponseError as e:
            if e.status == 429:
                await asyncio.sleep(1)
                await self.download_file(url, dest)
            else:
                raise e

    async def download_library(self, library, os_name, os_arch, version):
        need_extract = False
        if 'downloads' in library and 'artifact' in library['downloads']:
            artifact = library['downloads']['artifact']
            library_path = f".minecraft/libraries/{artifact['path']}"
        else:
            raise ValueError(f"依赖库{library}没有下载链接")

        if "rules" in library:
            for rule in library["rules"]:
                if "action" in rule:
                    is_allowed = rule["action"] == "allow"
                else:
                    is_allowed = False
                if "os" in rule and rule["os"]["name"] == os_name:
                    if is_allowed:
                        await self.download_file(artifact['url'], library_path)
                        need_extract = True
                    else:
                        return
                elif not "os" in rule:
                    raise ValueError(f"依赖库{library}没有指定操作系统")
        elif "classifiers" in library["downloads"]:
            classifier = library["downloads"]["classifiers"]
            for native in classifier:
                if native == f"natives-{os_name}":
                    await self.download_file(classifier[native]["url"], library_path)
                    need_extract = True
        else:
            await self.download_file(artifact['url'], library_path)
            return

        if need_extract:
            with zipfile.ZipFile(library_path, 'r') as zip_ref:
                extract_path = f".minecraft/versions/{version}/{version}-natives"
                os.makedirs(extract_path, exist_ok=True)
                for member in zip_ref.namelist():
                    if not (member.startswith('META-INF/') or member.endswith('/')):
                        if os_arch == "64" and ("86" in member or "32" in member or "arm" in member):
                            continue
                        elif os_arch == "86" and ("64" in member or "arm" in member):
                            continue
                        elif os_arch == "arm64" and ("86" in member or "32" in member):
                            continue
                        source = zip_ref.open(member)
                        extract_file_path = os.path.join(extract_path, os.path.basename(member))
                        target = open(extract_file_path, "wb")
                        with source, target:
                            shutil.copyfileobj(source, target)

    async def download_game_files(self, version_info, version):
        os_name = platform.system()
        if os_name == "Windows":
            os_name = "windows"
        elif os_name == "Linux":
            os_name = "linux"
        elif os_name == "Darwin":
            os_name = "osx"
        else:
            raise ValueError(f"不支持的操作系统{os_name}")

        os_arch = platform.architecture()[0]
        if os_arch == "64bit":
            os_arch = "64"
        elif os_arch == "32bit":
            os_arch = "86"
        elif os_arch == "arm64":
            os_arch = "arm64"

        libraries = version_info['libraries']
        tasks = []
        os.makedirs(f".minecraft/versions/{version}/{version}-natives", exist_ok=True)
        for library in libraries:
            tasks.append(self.download_library(library, os_name, os_arch, version))
        await asyncio.gather(*tasks)

    async def download_assets(self, version_info):
        asset_index_url = version_info['assetIndex']['url']
        asset_index_path = f".minecraft/assets/indexes/{version_info['assetIndex']['id']}.json"
        await self.download_file(asset_index_url, asset_index_path)

        async with aiofiles.open(asset_index_path, 'r') as file:
            asset_index = json.loads(await file.read())

        tasks = []
        for asset, info in asset_index['objects'].items():
            hash = info['hash']
            asset_url = f"https://resources.download.minecraft.net/{hash[:2]}/{hash}"
            asset_path = f".minecraft/assets/objects/{hash[:2]}/{hash}"
            tasks.append(self.download_file(asset_url, asset_path))
        await asyncio.gather(*tasks)

    async def download_version(self, version_info, version):
        await asyncio.gather(
            self.download_game_files(version_info, version),
            self.download_assets(version_info),
            self.download_file(version_info['downloads']['client']['url'], f".minecraft/versions/{version}/{version}.jar")
        )

async def main():
    connector = aiohttp.TCPConnector(limit_per_host=25)
    async with aiohttp.ClientSession(connector=connector) as session:
        version_manifest_url = "https://piston-meta.mojang.com/mc/game/version_manifest.json"
        version_manifest_path = ".minecraft/version_manifest.json"
        
        await DownloadClass(session).download_file(version_manifest_url, version_manifest_path)
        
        async with aiofiles.open(version_manifest_path, 'r') as file:
            version_manifest = json.loads(await file.read())
        
        latest_release = version_manifest['latest']['release']
        latest_snapshot = version_manifest['latest']['snapshot']
        versions = {version['id']: version['url'] for version in version_manifest['versions']}
        
        print(f"最新发布版本: {latest_release}")
        print(f"最新快照版本: {latest_snapshot}")
        
        selected_version = input("请输入要下载的版本: ")
        if selected_version not in versions:
            print("无效的版本号")
            return
        
        version_info_url = versions[selected_version]
        version_info_path = f".minecraft/versions/{selected_version}/{selected_version}.json"
        
        await DownloadClass(session).download_file(version_info_url, version_info_path)
        
        async with aiofiles.open(version_info_path, 'r') as file:
            version_info = json.loads(await file.read())
        
        downloader = DownloadClass(session)
        await downloader.download_version(version_info, selected_version)

if __name__ == "__main__":
    asyncio.run(main())