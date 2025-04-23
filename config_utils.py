import json
import os
from log_manager import logger as logging
import aiofiles
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# 定义全局变量
_config_cache = None
_last_modified_time = 0

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


class ConfigFileHandler(FileSystemEventHandler):
    def on_modified(self, event):
        global _config_cache, _last_modified_time
        config_path = 'QCL/config.json'  # 修改为实际的配置文件路径
        if event.src_path == os.path.abspath(config_path):
            try:
                with open(config_path, 'r') as f:
                    _config_cache = json.load(f)
                _last_modified_time = os.path.getmtime(config_path)
                logging.info("配置文件已更新，缓存已刷新")
            except Exception as e:
                logging.error(f"更新配置缓存时出错: {str(e)}")

def start_config_watcher():
    event_handler = ConfigFileHandler()
    observer = Observer()
    config_dir = os.path.dirname(os.path.abspath('QCL/config.json'))  # 修改为实际的配置文件路径
    # 移除不可达代码
    observer.schedule(event_handler, path=config_dir, recursive=False)
    observer.start()
    return observer
