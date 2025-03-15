import asyncio
import logging
import os
import platform
import re
from typing import Dict, Set, List

import psutil


def _check_rules(library, os_name, os_arch=None):
    for rule in library.get("rules", []):
        action = rule.get("action")
        if not action:
            raise ValueError(f"{library}的规则中缺少action字段")
        os_condition = rule.get("os")
        if action == "allow":
            if os_condition:
                if 'name' in os_condition and os_condition["name"] != os_name:
                    return False
                if 'arch' in os_condition and os_condition["arch"] is not None and os_condition["arch"] != os_arch:
                    return False
            return True
        elif action == "disallow":
            if os_condition:
                if 'name' in os_condition and os_condition["name"] == os_name:
                    return False
                if 'arch' in os_condition and os_condition["arch"] is not None and os_condition["arch"] == os_arch:
                    return False
        else:
            raise ValueError(f"{library}的规则中action字段不合法")
    return True


async def get_cp(version_info, version, os_name):
    cp = ""
    for library in version_info.get('libraries', []):
        artifact = library.get('downloads', {}).get('artifact')
        if artifact and _check_rules(library, os_name):
            lib_path = f".minecraft/libraries/{artifact['path']};"
            lib_path = os.path.abspath(lib_path)
            cp += lib_path
        classifiers = library.get('downloads', {}).get('classifiers')
        if classifiers:
            for native in classifiers:
                lib_path = f".minecraft/libraries/{classifiers[native]['path']};"
                lib_path = os.path.abspath(lib_path)
                cp += lib_path
    cp += f".minecraft/versions/{version}/{version}.jar"
    cp = f'"{cp}"'
    return cp


async def async_find_java() -> Dict[str, str]:
    java_executables = ("javaw.exe", "java.exe")
    keywords = {"java", "jdk", "jre", "oracle", "minecraft", "runtime"}
    ignore_dirs = {"windows", "program files", "system32", "temp"}
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
        raise ValueError(f"不支持的操作系统{os_name}")
    if os_name == "darwin":
        os_name = "osx"
    os_arch = platform.architecture()[0].replace("bit", "")
    if os_arch not in ["32", "64", "arm64"]:
        raise ValueError(f"不支持的操作系统架构{os_arch}")
    return os_name, os_arch
