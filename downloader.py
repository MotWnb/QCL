import asyncio
import json
import os
import re
import shutil
import zipfile

import aiofiles
import aiohttp

from utils import _check_rules


class DownloadClass:
    def __init__(self, session):
        self.session = session

    async def download_file(self, url, dest):
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
                    break
            except aiohttp.ClientResponseError as e:
                if e.status == 429:
                    await asyncio.sleep(1)
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
            library_path = f".minecraft/libraries/{artifact['path']}"
            await self.download_file(artifact['url'], library_path)
            need_extract = need_extract or "natives" in artifact['url']
            if need_extract:
                extract_path = f".minecraft/versions/{version}/{version}-natives"
                os.makedirs(extract_path, exist_ok=True)
                with zipfile.ZipFile(library_path, 'r') as zip_ref:
                    file_dict = {}
                    for member in zip_ref.namelist():
                        if member.startswith("META-INF/") or member.endswith("/") or member.endswith("LICENSE"):
                            continue
                        if (os_arch == "64" and any(x in member for x in ["86", "32", "arm"])) or (
                                os_arch == "32" and any(x in member for x in ["64", "arm"])) or (
                                os_arch == "arm64" and any(x in member for x in ["86", "32"])):
                            continue
                        extract_file_path = os.path.join(extract_path, os.path.basename(member))
                        file_dict[member] = extract_file_path

                    processed_dict = {}
                    keys_to_delete = []
                    for member, extract_file_path in file_dict.items():
                        if member.endswith("class"):
                            raise Exception("错误的natives")
                        file_name_main = re.sub(r'(x86|x64|x32|86|64|32)', '', member)
                        file_name_main = re.sub(r'[-_]', '', file_name_main)
                        file_name_main = re.sub(r'\.\w+$', '', file_name_main)
                        if file_name_main in processed_dict:
                            if len(member) > len(processed_dict[file_name_main][0]):
                                keys_to_delete.append(processed_dict[file_name_main][0])
                                processed_dict[file_name_main] = (member, extract_file_path)
                        else:
                            processed_dict[file_name_main] = (member, extract_file_path)

                    for key in keys_to_delete:
                        if key in file_dict:
                            del file_dict[key]
                    for member, extract_file_path in file_dict.items():
                        with zip_ref.open(member) as source, open(extract_file_path, "wb") as target:
                            shutil.copyfileobj(source, target)  # type:ignore

    async def download_game_files(self, version_info, version, os_name, os_arch):
        libraries = version_info.get('libraries', [])
        os.makedirs(f".minecraft/versions/{version}/{version}-natives", exist_ok=True)
        tasks = [self.download_library(library, os_name, os_arch, version) for library in libraries]
        await asyncio.gather(*tasks)

    async def download_assets(self, version_info):
        asset_index_url = version_info.get('assetIndex', {}).get('url')
        if asset_index_url:
            asset_index_id = version_info['assetIndex']['id']
            asset_index_path = f".minecraft/assets/indexes/{asset_index_id}.json"
            await self.download_file(asset_index_url, asset_index_path)

            async with aiofiles.open(asset_index_path, 'r') as file:
                asset_index = json.loads(await file.read())

            tasks = []
            for asset, info in asset_index.get('objects', {}).items():
                file_hash = info['hash']
                asset_url = f"https://resources.download.minecraft.net/{file_hash[:2]}/{file_hash}"
                asset_path = f".minecraft/assets/objects/{file_hash[:2]}/{file_hash}"
                tasks.append(self.download_file(asset_url, asset_path))
            await asyncio.gather(*tasks)

    async def download_version(self, version_info, version, os_name, os_arch):
        await asyncio.gather(self.download_game_files(version_info, version, os_name, os_arch),
                             self.download_assets(version_info),
                             self.download_file(version_info.get('downloads', {}).get('client', {}).get('url'),
                                                f".minecraft/versions/{version}/{version}.jar"),
                             self.download_log4j2(version_info, version))
