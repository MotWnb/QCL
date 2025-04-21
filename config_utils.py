import json
import os
from log_manager import logger as logging
import aiofiles

async def get_config():
    if os.path.exists("QCL/config.json"):
        async with aiofiles.open("QCL/config.json", 'r', encoding='utf-8') as f:
            content = await f.read()
            return json.loads(content)
    else:
        logging.warning("配置文件不存在，将使用默认配置")
        default_config = {
            "version_manifest_url": "https://piston-meta.mojang.com/mc/game/version_manifest.json",
            "version_manifest_path": ".minecraft/version_manifest.json",
            "resource_download_base_url": "https://resources.download.minecraft.net",
            "bmclapi_base_url": "https://bmclapi2.bangbang93.com",
            "minecraft_base_dir": ".minecraft",
            "java_executables": [
                "javaw.exe",
                "java.exe"
            ],
            "keywords": [
                "java",
                "jdk",
                "jre",
                "oracle",
                "minecraft",
                "runtime"
            ],
            "ignore_dirs": [
                "windows",
                "system32",
                "temp"
            ],
            "version_isolation_enabled": True,
            "use_mirror": False,
        }
        async with aiofiles.open("QCL/config.json", 'w', encoding='utf-8') as f:
            await f.write(json.dumps(default_config, indent=4))
        return default_config

async def save_config(config):
    async with aiofiles.open("QCL/config.json", 'w', encoding='utf-8') as f:
        await f.write(json.dumps(config, indent=4))

async def settings():
    config = await get_config()
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
