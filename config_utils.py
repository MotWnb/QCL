import json
import os
from pathlib import Path
from log_manager import logger as logging
import aiofiles
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import asyncio

_config_cache = None
_last_modified_time = None
_config_lock = asyncio.Lock()

async def get_config():
    global _config_cache, _last_modified_time
    async with _config_lock:
        config_path = PROJECT_ROOT / "QCL" / "config.json"
        if _config_cache and os.path.getmtime(config_path) == _last_modified_time:
            return _config_cache

        if config_path.exists():
            async with aiofiles.open(config_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                _config_cache = json.loads(content)
                _last_modified_time = os.path.getmtime(config_path)
                return _config_cache
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
            async with aiofiles.open(config_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(default_config, indent=4))
            _config_cache = default_config
            _last_modified_time = os.path.getmtime(config_path)
            return default_config

async def save_config(config):
    config_path = PROJECT_ROOT / "QCL" / "config.json"
    async with aiofiles.open(config_path, 'w', encoding='utf-8') as f:
        await f.write(json.dumps(config, indent=4))
    global _config_cache, _last_modified_time
    _config_cache = config
    _last_modified_time = os.path.getmtime(config_path)

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
    def __init__(self, loop):
        self.loop = loop

    def on_modified(self, event):
        async def async_on_modified():
            global _config_cache, _last_modified_time
            config_path = PROJECT_ROOT / "QCL" / "config.json"
            if event.src_path == str(config_path.resolve()):
                try:
                    async with aiofiles.open(config_path, 'r') as f:
                        content = await f.read()
                        _config_cache = json.loads(content)
                    _last_modified_time = os.path.getmtime(config_path)
                    logging.info("配置文件已更新，缓存已刷新")
                except Exception as e:
                    logging.error(f"更新配置缓存时出错: {str(e)}")
        self.loop.create_task(async_on_modified())

def start_config_watcher():
    loop = asyncio.get_running_loop()
    event_handler = ConfigFileHandler(loop)
    observer = Observer()
    config_dir = (PROJECT_ROOT / "QCL").resolve()
    observer.schedule(event_handler, path=config_dir, recursive=False)
    observer.start()
    return observer
