import asyncio
import json
import os

import aiofiles
import aiohttp

from utils import get_os_info
from downloader import DownloadClass
from launcher import MinecraftLauncher

async def main():
    os_name, os_arch = await get_os_info()
    user_choice = input("请输入你想要的操作:\n1. 下载\n2. 启动\n")
    connector = aiohttp.TCPConnector(limit_per_host=1024)
    async with aiohttp.ClientSession(connector=connector) as session:
        version_manifest_url = "https://piston-meta.mojang.com/mc/game/version_manifest.json"
        version_manifest_path = ".minecraft/version_manifest.json"
        downloader = DownloadClass(session)

        if user_choice == "1":
            await downloader.download_file(version_manifest_url, version_manifest_path)

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
            await downloader.download_file(version_info_url, version_info_path)

            async with aiofiles.open(version_info_path, 'r') as file:
                version_info = json.loads(await file.read())

            await downloader.download_version(version_info, selected_version, os_name, os_arch)
        elif user_choice == "2":
            versions = os.listdir(".minecraft/versions")
            version = input(f"请输入要启动的版本: {versions}\n")
            version_info_path = f".minecraft/versions/{version}/{version}.json"
            async with aiofiles.open(version_info_path, 'r') as file:
                version_info = json.loads(await file.read())
            launcher = MinecraftLauncher()
            await launcher.launcher(version_info, version)

if __name__ == "__main__":
    asyncio.run(main())