import asyncio
import hashlib
import os
import platform
import re
import shutil
import struct
import zipfile
from typing import Dict, Set, List
import json
import aiofiles
import psutil
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler


# ================= 配置管理接口与实现 =================
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

    async def get_config(self):
        async with self._config_lock:
            config_path = self.PROJECT_ROOT / "QCL" / "config.json"
            if self._config_cache and os.path.getmtime(config_path) == self._last_modified_time:
                return self._config_cache
            if config_path.exists():
                try:
                    self._config_cache = await read_json_file(str(config_path))
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
                ensure_dir_exists(str(config_path.parent))
                await write_json_file(str(config_path), default_config)
                self._config_cache = default_config
                self._last_modified_time = os.path.getmtime(config_path)
                return default_config

    async def save_config(self, config):
        config_path = self.PROJECT_ROOT / "QCL" / "config.json"
        await write_json_file(str(config_path), config)
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

    def start_config_watcher(self):
        # 可选：如需热更新功能可在此实现
        pass
import asyncio
import hashlib
import os
import platform
import re
import shutil
import struct
import zipfile
from typing import Dict, Set, List
import json
import aiofiles
import psutil

import logging
from logging.handlers import RotatingFileHandler


def setup_logger():
    qcl_logger = logging.getLogger("QCL")
    qcl_logger.setLevel(logging.DEBUG)
    log_dir = "QCL"
    log_file = os.path.join(log_dir, "debug.log")
    old_log_file = os.path.join(log_dir, "log.old")
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    if os.path.exists(log_file):
        if os.path.exists(old_log_file):
            os.remove(old_log_file)
        os.rename(log_file, old_log_file)
    file_handler = RotatingFileHandler(
        log_file, maxBytes=1024 * 1024 * 10, backupCount=5
    )
    file_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(logging.DEBUG)
    console_handler = logging.StreamHandler()
    console_formatter = logging.Formatter("%(message)s")
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(logging.INFO)
    if not qcl_logger.hasHandlers():
        qcl_logger.addHandler(file_handler)
        qcl_logger.addHandler(console_handler)
    return qcl_logger


logger = setup_logger()


class IUtils:
    def check_rules(self, element, os_name, os_arch=None, features=None):
        pass

    async def get_cp(
        self, version_info, version, os_name, os_arch, version_directory, config
    ):
        pass

    async def async_find_java(self, config):
        pass

    async def get_os_info(self):
        pass

    def check_library_arch_from_content(self, file_content, required_arch):
        pass

    def sync_extract(self, library_path, extract_path):
        pass

    async def calculate_sha1(self, file_path):
        pass


class Utils(IUtils):
    def check_rules(self, element, os_name, os_arch=None, features=None):
        rules = element.get("rules", [])
        if not rules:
            return True
        for rule in rules:
            os_cond = rule.get("os", {})
            feature_cond = rule.get("features", {})
            action = rule.get("action", "allow")
            os_match = True
            if "name" in os_cond and os_cond["name"] != os_name:
                os_match = False
            if "arch" in os_cond and os_cond.get("arch") not in (None, os_arch):
                os_match = False
            feature_match = True
            if features is not None:
                for feat_key, feat_val in feature_cond.items():
                    if features.get(feat_key) != feat_val:
                        feature_match = False
            if os_match and feature_match:
                return action == "allow"
        return False

    async def get_cp(
        self, version_info, version, os_name, os_arch, version_directory, config
    ):
        cp = ""
        for library in version_info.get("libraries", []):
            if not self.check_rules(library, os_name):
                continue
            artifact = library.get("downloads", {}).get("artifact")
            if artifact:
                lib_path = str(
                    os.path.join(
                        config["minecraft_base_dir"], "libraries", artifact["path"]
                    )
                    + ";"
                )
                cp += os.path.abspath(lib_path)
            classifiers = library.get("downloads", {}).get("classifiers")
            if classifiers:
                natives = library.get("natives", {})
                if os_name in natives:
                    native_classifier = natives[os_name].replace("${arch}", os_arch)
                else:
                    native_classifier = f"natives-{os_name}"
                if native_classifier in classifiers:
                    info = classifiers[native_classifier]
                    lib_path = str(
                        os.path.join(
                            config["minecraft_base_dir"], "libraries", info["path"]
                        )
                        + ";"
                    )
                    cp += os.path.abspath(lib_path)
        main_jar_path = os.path.join(version_directory, f"{version}.jar")
        main_jar_path = os.path.abspath(main_jar_path)
        cp += main_jar_path
        return f'"{cp}"'

    async def async_find_java(self, config):
        java_executables = config["java_executables"]
        keywords = config["keywords"]
        ignore_dirs = config["ignore_dirs"]
        scanned_paths: Set[str] = set()
        java_versions: Dict[str, str] = {}

        async def safe_scandir(dir_path: str) -> List[os.DirEntry]:
            try:
                entries: List[os.DirEntry] = await asyncio.to_thread(
                    lambda: list(os.scandir(dir_path))
                )
                return entries
            except (PermissionError, FileNotFoundError, NotADirectoryError):
                return []
            except Exception as e:
                logger.debug(f"Scan error in {dir_path}: {str(e)}")
                return []

        async def scan_path(dir_path: str, depth: int = 0) -> None:
            if (
                depth > 4
                or not os.path.isdir(dir_path)
                or any(ign in dir_path.lower() for ign in ignore_dirs)
            ):
                return
            try:
                entries = await safe_scandir(dir_path)
                for entry in entries:
                    entry_path = entry.path.replace("\\", "/")
                    if entry.is_file() and entry.name.lower() in java_executables:
                        parent_dir = os.path.dirname(entry_path)
                        if parent_dir not in scanned_paths:
                            scanned_paths.add(parent_dir)
                            version = await get_java_version(parent_dir)
                            java_versions[parent_dir] = version
                            logger.info(f"Found Java {version} at {parent_dir}")
                        continue
                    if entry.is_dir() and not entry.name.startswith("."):
                        dir_name = entry.name.lower()
                        if any(kw in dir_name for kw in keywords) or depth < 2:
                            await scan_path(str(entry_path), depth + 1)
            except Exception as e:
                logger.debug(f"Error processing {dir_path}: {str(e)}")

        async def get_java_version(java_dir: str) -> str:
            java_exe = os.path.join(java_dir, "java.exe")
            if not os.path.exists(java_exe):
                return "unknown"
            try:
                proc = await asyncio.create_subprocess_exec(
                    java_exe,
                    "-version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
                output = (stderr or stdout).decode("utf-8", errors="ignore").lower()
                version_match = re.search(
                    r'version "(\d+)(?:\.\d+)?(?:\.[\d_]+)?(?:-[a-zA-Z0-9]+)?"', output
                )
                if version_match:
                    major_version = version_match.group(1)
                    if major_version == "1":
                        minor_match = re.search(r'"1\.(\d+)\.', output)
                        if minor_match:
                            return f"Java {minor_match.group(1)}"
                        else:
                            return "Java 8"
                    return f"Java {major_version}"
            except (asyncio.TimeoutError, FileNotFoundError):
                return "timeout"
            except Exception as e:
                logger.debug(f"Version check failed: {str(e)}")
            return "unknown"

        scan_tasks = []
        for env_var in ["PATH", "JAVA_HOME"]:
            if paths := os.getenv(env_var, ""):
                for env_path in (
                    p.strip() for p in paths.split(os.pathsep) if p.strip()
                ):
                    abs_path = os.path.abspath(env_path)
                    scan_tasks.append(scan_path(abs_path))

        special_paths = [
            *(p.mountpoint for p in psutil.disk_partitions() if p.fstype),
            os.getenv("APPDATA", ""),
            os.getenv("LOCALAPPDATA", ""),
            os.getcwd(),
        ]
        for path in filter(os.path.isdir, special_paths):
            scan_tasks.append(scan_path(path))

        await asyncio.gather(*scan_tasks)
        return java_versions

    async def get_os_info(self):
        os_name = platform.system().lower()
        if os_name not in ["windows", "linux", "darwin"]:
            logger.error(f"不支持的操作系统{os_name}")
            raise ValueError(f"不支持的操作系统{os_name}")
        if os_name == "darwin":
            os_name = "osx"
        os_arch = platform.architecture()[0].replace("bit", "")
        if os_arch not in ["32", "64", "arm64"]:
            logger.error(f"不支持的操作系统架构{os_arch}")
            raise ValueError(f"不支持的操作系统架构{os_arch}")
        return os_name, os_arch

    def check_library_arch_from_content(self, file_content, required_arch):
        try:
            header = file_content[:64]
            if header[:4] == b"\x7fELF":
                ei_class = header[4]
                if ei_class == 1 and required_arch == "32":
                    return True
                elif ei_class == 2 and required_arch == "64":
                    return True
                else:
                    return False
            elif header[:2] == b"MZ":
                pe_offset = struct.unpack("<I", header[0x3C:0x40])[0]
                pe_header = file_content[pe_offset : pe_offset + 6]
                machine_type = struct.unpack("<H", pe_header[4:6])[0]
                if machine_type == 0x014C and required_arch == "32":
                    return True
                elif machine_type == 0x8664 and required_arch == "64":
                    return True
                elif machine_type == 0xAA64 and required_arch == "arm64":
                    return True
                else:
                    return False
            else:
                logger.error("不是有效的 so 或 dll 文件")
                return False
        except Exception as e:
            logger.error(f"检查架构时出错: {str(e)}")
            return False

    def sync_extract(self, library_path, extract_path):
        system_arch = platform.architecture()[0]
        if "64" in system_arch:
            required_arch = "64"
        else:
            required_arch = "32"
        with zipfile.ZipFile(library_path, "r") as zip_ref:
            filtered_members = []
            for member in zip_ref.namelist():
                skip_reasons = []
                if member.startswith("META-INF/"):
                    skip_reasons.append("签名文件")
                if member.endswith("/"):
                    skip_reasons.append("空目录")
                if "LICENSE" in member.upper():
                    skip_reasons.append("许可证文件")
                if not skip_reasons:
                    try:
                        file_content = zip_ref.read(member)
                        if not self.check_library_arch_from_content(
                            file_content, required_arch
                        ):
                            skip_reasons.append("架构不匹配")
                    except Exception as e:
                        logger.error(f"检查 {member} 架构时出错: {str(e)}")
                        skip_reasons.append("架构检查出错")
                if skip_reasons:
                    logger.debug(f"跳过文件 {member}，原因: {', '.join(skip_reasons)}")
                    continue
                else:
                    logger.debug(f"保留文件 {member}")
                filtered_members.append(member)
            logger.debug(f"过滤完成,保留{filtered_members}")
            for member in filtered_members:
                file_name = os.path.basename(member)
                target_path = os.path.join(extract_path, file_name)
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                with zip_ref.open(member) as source, open(target_path, "wb") as target:
                    shutil.copyfileobj(source, target)  # type:ignore
                if os.name != "nt":
                    file_info = zip_ref.getinfo(member)
                    os.chmod(target_path, file_info.external_attr >> 16)

    async def calculate_sha1(self, file_path: str) -> str:
        sha1 = hashlib.sha1()
        async with aiofiles.open(file_path, "rb") as f:
            while True:
                chunk = await f.read(8192)
                if not chunk:
                    break
                sha1.update(chunk)
        return sha1.hexdigest()


async def read_json_file(path: str):
    async with aiofiles.open(path, "r", encoding="utf-8") as f:
        return json.loads(await f.read())


async def write_json_file(path: str, data):
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(json.dumps(data, indent=4))


def ensure_dir_exists(path: str):
    if not os.path.exists(path):
        os.makedirs(path)
