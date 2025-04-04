import asyncio
import json
import os
import logging

import aiofiles
import aiohttp

from utils import get_os_info
from downloader import DownloadClass
from launcher import MinecraftLauncher

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

async def main():
    os_name, os_arch = await get_os_info()
    user_choice = input("请输入你想要的操作:\n1. 下载\n2. 启动\n")
    connector = aiohttp.TCPConnector(limit_per_host=1024)
    async with aiohttp.ClientSession(connector=connector) as session:
        version_manifest_url = "https://piston-meta.mojang.com/mc/game/version_manifest.json"
        version_manifest_path = ".minecraft/version_manifest.json"
        downloader = DownloadClass(session)

        if user_choice == "1":
            logging.info("开始下载版本清单")
            await downloader.download_file(version_manifest_url, version_manifest_path)

            async with aiofiles.open(version_manifest_path, 'r') as file:
                version_manifest = json.loads(await file.read())

            latest_release = version_manifest['latest']['release']
            latest_snapshot = version_manifest['latest']['snapshot']
            versions = {version['id']: version['url'] for version in version_manifest['versions']}

            logging.info(f"最新发布版本: {latest_release}")
            logging.info(f"最新快照版本: {latest_snapshot}")

            selected_version = input("请输入要下载的版本: ")
            if selected_version not in versions:
                logging.error("无效的版本号")
                return

            version_info_url = versions[selected_version]
            version_info_path = f".minecraft/versions/{selected_version}/{selected_version}.json"
            logging.info(f"开始下载版本 {selected_version} 的信息")
            await downloader.download_file(version_info_url, version_info_path)

            async with aiofiles.open(version_info_path, 'r') as file:
                version_info = json.loads(await file.read())

            logging.info(f"开始下载版本 {selected_version} 的所有文件")
            await downloader.download_version(version_info, selected_version, os_name, os_arch)
        elif user_choice == "2":
            versions = os.listdir(".minecraft/versions")
            version = input(f"请输入要启动的版本: {versions}\n")
            version_info_path = f".minecraft/versions/{version}/{version}.json"
            original_game_directory = os.path.abspath(".minecraft")
            version_directory = os.path.join(original_game_directory, "versions", version)

            # 新增版本隔离选择
            version_isolation = input("是否开启版本隔离？(y/n): ").strip().lower() == 'y'
            if version_isolation:
                version_cwd = os.path.abspath(version_directory)
            else:
                # 获取QCL目录路径
                qcl_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "QCL")
                # 如果QCL目录不存在，则创建它
                os.makedirs(qcl_dir, exist_ok=True)
                version_cwd = qcl_dir

            async with aiofiles.open(version_info_path, 'r') as file:
                version_info = json.loads(await file.read())
            launcher = MinecraftLauncher()
            logging.info(f"开始启动版本 {version}")
            await launcher.launcher(version_info, version, version_cwd, version_isolation)


if __name__ == "__main__":
    asyncio.run(main())
    