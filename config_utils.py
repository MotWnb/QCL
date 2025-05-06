import json
import os
from pathlib import Path
from log_manager import logger
import aiofiles
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import asyncio

class IConfigManager:
    async def get_config(self): pass
    async def save_config(self, config): pass
    async def settings(self): pass
    def start_config_watcher(self): pass

class ConfigManager(IConfigManager):
    _config_cache = None
    _last_modified_time = None
    _config_lock = asyncio.Lock()
    PROJECT_ROOT = Path(__file__).parent

    @staticmethod
    async def read_config_file(config_path):
        async with aiofiles.open(config_path, 'r', encoding='utf-8') as f:
            content = await f.read()
            return json.loads(content)

    async def get_config(self):
        async with self._config_lock:
            config_path = self.PROJECT_ROOT / "QCL" / "config.json"
            if self._config_cache and os.path.getmtime(config_path) == self._last_modified_time: return self._config_cache
            if config_path.exists():
                try:
                    self._config_cache = await self.read_config_file(config_path)
                    self._last_modified_time = os.path.getmtime(config_path)
                    return self._config_cache
                except Exception as e:
                    logger.error(f"读取配置文件出错: {str(e)}")
                    return None
            else:
                logger.warning("配置文件不存在，将使用默认配置")
                default_config = {
                    "version_manifest_url": "https://piston-meta.mojang.com/mc/game/version_manifest.json",
                    "version_manifest_path": ".minecraft/version_manifest.json",
                    "resource_download_base_url": "https://resources.download.minecraft.net",
                    "bmclapi_base_url": "https://bmclapi2.bangbang93.com",
                    "minecraft_base_dir": ".minecraft",
                    "java_executables": ["javaw.exe", "java.exe"],
                    "keywords": ["java", "jdk", "jre", "oracle", "minecraft", "runtime"],
                    "ignore_dirs": ["windows", "system32", "temp"],
                    "version_isolation_enabled": True,
                    "use_mirror": False,
                }
                async with aiofiles.open(config_path, 'w', encoding='utf-8') as f:
                    await f.write(json.dumps(default_config, indent=4))
                self._config_cache = default_config
                self._last_modified_time = os.path.getmtime(config_path)
                return default_config

    async def save_config(self, config):
        config_path = self.PROJECT_ROOT / "QCL" / "config.json"
        async with aiofiles.open(config_path, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(config, indent=4))
        await asyncio.sleep(0.1)
        self._config_cache = config
        self._last_modified_time = os.path.getmtime(config_path)

    async def settings(self):
        config = await self.get_config()
        print("当前版本隔离状态: ", "开启" if config["version_isolation_enabled"] else "关闭")
        choice = input("是否开启版本隔离？(y/n): ").strip().lower()
        if choice == 'y': config["version_isolation_enabled"] = True
        elif choice == 'n': config["version_isolation_enabled"] = False
        print("当前是否使用镜像源: ", "是" if config["use_mirror"] else "否")
        choice = input("是否使用镜像源？(y/n): ").strip().lower()
        if choice == 'y': config["use_mirror"] = True
        elif choice == 'n': config["use_mirror"] = False
        await self.save_config(config)

    class ConfigFileHandler(FileSystemEventHandler):
        def __init__(self, loop, project_root):
            self.loop = loop
            self.PROJECT_ROOT = project_root

        def on_modified(self, event):
            async def async_on_modified():
                config_path = self.PROJECT_ROOT / "QCL" / "config.json"
                if event.src_path == str(config_path.resolve()):
                    try:
                        async with aiofiles.open(config_path, 'r', encoding='utf-8') as f:
                            content = await f.read()
                            if content.strip():
                                ConfigManager._config_cache = json.loads(content)
                                ConfigManager._last_modified_time = os.path.getmtime(config_path)
                                logger.info("配置文件已更新，缓存已刷新")
                            else:
                                logger.warning("配置文件内容为空，跳过缓存更新")
                    except Exception as e:
                        logger.error(f"更新配置缓存时出错: {str(e)}")
            self.loop.create_task(async_on_modified())

    def start_config_watcher(self):
        loop = asyncio.get_running_loop()
        event_handler = self.ConfigFileHandler(loop, self.PROJECT_ROOT)
        observer = Observer()
        config_dir = (self.PROJECT_ROOT / "QCL").resolve()
        observer.schedule(event_handler, path=config_dir, recursive=False)
        observer.start()
        return observer