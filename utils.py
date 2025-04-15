import asyncio
import hashlib
import json
import os
import platform
import re
from typing import Dict, Set, List

import aiofiles
import psutil

from log_manager import logger as logging

# 读取配置文件
with open('config.json', 'r') as f:
    config = json.load(f)


def _check_rules(element, os_name, os_arch=None, features=None):
    rules = element.get("rules", [])
    if not rules:
        return True

    for rule in rules:
        os_cond = rule.get("os", {})
        feature_cond = rule.get("features", {})
        action = rule.get("action", "allow")

        # 检查 OS 条件
        os_match = True
        if 'name' in os_cond and os_cond['name'] != os_name:
            os_match = False
        if 'arch' in os_cond and os_cond.get('arch') not in (None, os_arch):
            os_match = False

        # 检查特性条件
        feature_match = True
        if features is not None:
            for feat_key, feat_val in feature_cond.items():
                if features.get(feat_key) != feat_val:
                    feature_match = False

        # 规则条件满足时返回对应动作
        if os_match and feature_match:
            return action == "allow"

    return False  # 无匹配规则，默认拒绝


async def get_cp(version_info, version, os_name, os_arch, version_directory):
    cp = ""
    for library in version_info.get('libraries', []):
        if not _check_rules(library, os_name):
            continue

        # 处理主 artifact
        artifact = library.get('downloads', {}).get('artifact')
        if artifact:
            lib_path = str(os.path.join(config['minecraft_base_dir'], 'libraries', artifact['path']) + ';')
            cp += os.path.abspath(lib_path)

        # 处理 classifier（natives）
        classifiers = library.get('downloads', {}).get('classifiers')
        if classifiers:
            natives = library.get("natives", {})
            if os_name in natives:
                native_classifier = natives[os_name].replace("${arch}", os_arch)
            else:
                native_classifier = f"natives-{os_name}"

            if native_classifier in classifiers:
                info = classifiers[native_classifier]
                lib_path = str(os.path.join(config['minecraft_base_dir'], 'libraries', info['path']) + ';')
                cp += os.path.abspath(lib_path)

    # 使用传入的version_directory构建路径
    main_jar_path = os.path.join(version_directory, f"{version}.jar")
    main_jar_path = os.path.abspath(main_jar_path)
    cp += main_jar_path
    return f'"{cp}"'


async def async_find_java() -> Dict[str, str]:
    java_executables = config['java_executables']
    keywords = config['keywords']
    ignore_dirs = config['ignore_dirs']
    scanned_paths: Set[str] = set()
    java_versions: Dict[str, str] = {}

    async def safe_scandir(path: str) -> List[os.DirEntry]:
        try:
            entries: List[os.DirEntry] = await asyncio.to_thread(
                lambda: list(os.scandir(path))
            )
            return entries
        except (PermissionError, FileNotFoundError, NotADirectoryError):
            return []
        except Exception as e:
            logging.debug(f"Scan error in {path}: {str(e)}")
            return []

    async def scan_path(path: str, depth: int = 0) -> None:
        if depth > 4 or not os.path.isdir(path) or any(ign in path.lower() for ign in ignore_dirs):
            return

        try:
            entries = await safe_scandir(path)
            for entry in entries:
                entry_path = entry.path.replace("\\", "/")
                if entry.is_file() and entry.name.lower() in java_executables:
                    parent_dir = os.path.dirname(entry_path)
                    if parent_dir not in scanned_paths:
                        scanned_paths.add(parent_dir)
                        version = await get_java_version(parent_dir)
                        java_versions[parent_dir] = version
                        logging.info(f"Found Java {version} at {parent_dir}")
                    continue
                if entry.is_dir() and not entry.name.startswith("."):
                    dir_name = entry.name.lower()
                    if any(kw in dir_name for kw in keywords) or depth < 2:
                        await scan_path(str(entry_path), depth + 1)
        except Exception as e:
            logging.debug(f"Error processing {path}: {str(e)}")

    async def get_java_version(path: str) -> str:
        java_exe = os.path.join(path, "java.exe")
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
                r'version "(\d+)(?:\.\d+)?(?:\.[\d_]+)?(?:-[a-zA-Z0-9]+)?"',
                output
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
            logging.debug(f"Version check failed: {str(e)}")
        return "unknown"

    scan_tasks = []
    for env_var in ["PATH", "JAVA_HOME"]:
        if paths := os.getenv(env_var, ""):
            for path in (p.strip() for p in paths.split(os.pathsep) if p.strip()):
                abs_path = os.path.abspath(path)
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


async def get_os_info():
    os_name = platform.system().lower()
    if os_name not in ["windows", "linux", "darwin"]:
        logging.error(f"不支持的操作系统{os_name}")
        raise ValueError(f"不支持的操作系统{os_name}")
    if os_name == "darwin":
        os_name = "osx"
    os_arch = platform.architecture()[0].replace("bit", "")
    if os_arch not in ["32", "64", "arm64"]:
        logging.error(f"不支持的操作系统架构{os_arch}")
        raise ValueError(f"不支持的操作系统架构{os_arch}")
    return os_name, os_arch


async def calculate_sha1(file_path: str) -> str:
    """异步计算文件的SHA1哈希值"""
    sha1 = hashlib.sha1()
    async with aiofiles.open(file_path, 'rb') as f:
        while True:
            chunk = await f.read(8192)
            if not chunk:
                break
            sha1.update(chunk)
    return sha1.hexdigest()
    