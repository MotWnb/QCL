import asyncio
import json
import os
import shutil

import aiofiles
import aiohttp

from config_utils import get_config, settings
from downloader import DownloadClass
from launcher import MinecraftLauncher
from log_manager import logger as logging
from utils import get_os_info


async def main():
    config = await get_config()
    # 启动时删除 .temp 目录
    temp_path = os.path.join(config['minecraft_base_dir'], '.temp')
    if os.path.exists(temp_path):
        shutil.rmtree(temp_path)
        logging.info("已删除临时目录 .temp")

    os_name, os_arch = await get_os_info()
    while True:
        user_choice = input("请输入你想要的操作:\n1. 下载\n2. 启动\n3. 设置\n4. 退出\n")
        connector = aiohttp.TCPConnector(limit_per_host=1024)
        async with aiohttp.ClientSession(connector=connector) as session:
            version_manifest_url = config['version_manifest_url']
            version_manifest_path = config['version_manifest_path']
            downloader = DownloadClass(session, config)  # 传递 config 参数

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
                    continue

                version_info_url = versions[selected_version]
                version_info_path = os.path.join(config['minecraft_base_dir'], 'versions', selected_version,
                                                 f"{selected_version}.json")
                logging.info(f"开始下载版本 {selected_version} 的信息")
                await downloader.download_file(version_info_url, version_info_path)

                async with aiofiles.open(version_info_path, 'r') as file:
                    version_info = json.loads(await file.read())

                logging.info(f"开始下载版本 {selected_version} 的所有文件")
                await downloader.download_version(version_info, selected_version, os_name, os_arch)
            elif user_choice == "2":
                versions = os.listdir(os.path.join(config['minecraft_base_dir'], 'versions'))
                version = input(f"请输入要启动的版本: {versions}\n")
                version_info_path = os.path.join(config['minecraft_base_dir'], 'versions', version, f"{version}.json")
                original_game_directory = os.path.abspath(config['minecraft_base_dir'])
                version_directory = os.path.join(original_game_directory, "versions", version)

                version_isolation_enabled = config["version_isolation_enabled"]
                if version_isolation_enabled:
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
                await launcher.launcher(version_info, version, version_cwd, version_isolation_enabled, config)  # 传递 config 参数
            elif user_choice == "3":
                await settings()
            elif user_choice == "4":
                break
            else:
                logging.error("无效的选择，请重新输入。")


if __name__ == "__main__":
    asyncio.run(main())
