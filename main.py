import asyncio
import json
import os
import logging

import aiofiles
import aiohttp

from utils import get_os_info
from downloader import DownloadClass
from launcher import MinecraftLauncher

# 读取配置文件
with open('config.json', 'r') as f:
    config = json.load(f)

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

async def save_config(config):
    with open('config.json', 'w') as f:
        json.dump(config, f, indent=4)

async def settings():
    global config
    print("当前版本隔离状态: ", "开启" if config["version_isolation_enabled"] else "关闭")
    choice = input("是否开启版本隔离？(y/n): ").strip().lower()
    if choice == 'y':
        config["version_isolation_enabled"] = True
    elif choice == 'n':
        config["version_isolation_enabled"] = False

    print("当前是否使用镜像源: ", "是" if config["use_mirror"] else "否")
    choice = input("是否使用镜像源？(y/n): ").strip().lower()
    if choice == 'y':
        config["use_mirror"] = True
    elif choice == 'n':
        config["use_mirror"] = False

    await save_config(config)

async def main():
    os_name, os_arch = await get_os_info()
    while True:
        user_choice = input("请输入你想要的操作:\n1. 下载\n2. 启动\n3. 设置\n4. 退出\n")
        connector = aiohttp.TCPConnector(limit_per_host=1024)
        async with aiohttp.ClientSession(connector=connector) as session:
            version_manifest_url = config['version_manifest_url']
            version_manifest_path = config['version_manifest_path']
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
                    continue

                version_info_url = versions[selected_version]
                version_info_path = os.path.join(config['minecraft_base_dir'], 'versions', selected_version, f"{selected_version}.json")
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
                await launcher.launcher(version_info, version, version_cwd, version_isolation_enabled)
            elif user_choice == "3":
                await settings()
            elif user_choice == "4":
                break
            else:
                logging.error("无效的选择，请重新输入。")

if __name__ == "__main__":
    asyncio.run(main())
    