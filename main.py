import asyncio
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
import webbrowser
import zipfile
from platform import java_ver
from threading import Thread
from typing import Dict, Set, List, Callable

import aiofiles
import aiohttp
import pyperclip
import logging
import psutil


# 定义下载类，封装下载相关操作
def _check_rules(library, os_name, os_arch=None):
    for rule in library.get("rules", []):
        action = rule.get("action")
        if not action:
            raise ValueError(f"{library}的规则中缺少action字段")
        os_condition = rule.get("os")
        if action == "allow":
            if os_condition:
                if 'name' in os_condition:
                    if os_condition["name"] != os_name:
                        return False

                if 'arch' in os_condition and os_condition["arch"] is not None:
                    if os_condition["arch"] != os_arch:
                        return False
                return True

        elif action == "disallow":
            if os_condition:
                if 'name' in os_condition:
                    if os_condition["name"] == os_name:
                        return False
                if 'arch' in os_condition and os_condition["arch"] is not None:
                    if os_condition["arch"] == os_arch:
                        return False

        else:
            raise ValueError(f"{library}的规则中action字段不合法")
    return True


async def _xsts_auth(xbl_token):
    # Xbox Live 二级认证 (XSTS)
    xsts_url = "https://xsts.auth.xboxlive.com/xsts/authorize"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    payload = {
        "Properties": {
            "SandboxId": "RETAIL",
            "UserTokens": [xbl_token]
        },
        "RelyingParty": "rp://api.minecraftservices.com/",
        "TokenType": "JWT"
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(xsts_url, json=payload, headers=headers) as resp:
            if resp.status != 200:
                raise Exception("XSTS authentication failed")
            return (await resp.json())['Token']


async def _minecraft_auth(uhs, xsts_token):
    # Minecraft 认证
    mc_url = "https://api.minecraftservices.com/authentication/login_with_xbox"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    payload = {
        "identityToken": f"XBL3.0 x={uhs};{xsts_token}"
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(mc_url, json=payload, headers=headers) as resp:
            if resp.status != 200:
                raise Exception("Minecraft authentication failed")
            return (await resp.json())['access_token']


class MinecraftAuthenticator:
    def __init__(self, client_id="de243363-2e6a-44dc-82cb-ea8d6b5cd98d", refresh_token=None):
        self.client_id = client_id
        self.refresh_token = refresh_token
        self.access_token = None
        self.minecraft_access_token = None
        self.username = None
        self.uuid = None

    async def _get_device_code(self):
        code_pair_url = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
        payload = {
            "client_id": self.client_id,
            "scope": "XboxLive.signin offline_access",
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        async with aiohttp.ClientSession() as session:
            async with session.post(code_pair_url, data=payload, headers=headers) as resp:
                data = await resp.json()
                if resp.status != 200:
                    raise Exception(f"Failed to get device code: {data}")
                return data

    async def _wait_for_authorization(self, device_code, interval, expires_in):
        token_url = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
        payload = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": self.client_id,
            "device_code": device_code,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        start_time = time.time()

        async with aiohttp.ClientSession() as session:
            while True:
                if time.time() - start_time > expires_in:
                    raise Exception("Device code expired")

                async with session.post(token_url, data=payload, headers=headers) as resp:
                    data = await resp.json()

                if resp.status == 200:
                    self.access_token = data.get("access_token")
                    self.refresh_token = data.get("refresh_token")
                    return
                elif data.get("error") == "authorization_pending":
                    await asyncio.sleep(interval)
                elif data.get("error") == "slow_down":
                    interval += 5
                    await asyncio.sleep(interval)
                else:
                    raise Exception(f"Authorization failed: {data.get('error')}")

    async def _xbox_live_auth(self):
        # Xbox Live 一级认证
        xbox_url = "https://user.auth.xboxlive.com/user/authenticate"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        payload = {
            "Properties": {
                "AuthMethod": "RPS",
                "SiteName": "user.auth.xboxlive.com",
                "RpsTicket": f"d={self.access_token}"
            },
            "RelyingParty": "http://auth.xboxlive.com",
            "TokenType": "JWT"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(xbox_url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    raise Exception("Xbox Live authentication failed")
                data = await resp.json()
                return data['Token'], data['DisplayClaims']['xui'][0]['uhs']

    async def _verify_ownership(self):
        # 验证游戏所有权
        url = "https://api.minecraftservices.com/entitlements/mcstore"
        headers = {"Authorization": f"Bearer {self.minecraft_access_token}"}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200 or not (await resp.json()).get('items'):
                    raise Exception("Game ownership verification failed")

    async def _get_profile(self):
        # 获取玩家档案
        url = "https://api.minecraftservices.com/minecraft/profile"
        headers = {"Authorization": f"Bearer {self.minecraft_access_token}"}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    raise Exception("Failed to get player profile")
                data = await resp.json()
                self.username = data['name']
                self.uuid = data['id']

    async def authenticate(self):
        """完整的认证流程"""
        # 设备代码流登录
        if not self.refresh_token:
            code_data = await self._get_device_code()
            print(f"请访问 {code_data['verification_uri']} 输入代码: {code_data['user_code']}")
            pyperclip.copy(code_data['user_code'])
            webbrowser.open(code_data['verification_uri'])
            await self._wait_for_authorization(
                code_data['device_code'],
                code_data['interval'],
                code_data['expires_in']
            )

        # 如果已有刷新令牌，优先使用
        if self.refresh_token and not self.access_token:
            await self.refresh_token()

        # Xbox 认证流程
        xbl_token, uhs = await self._xbox_live_auth()
        xsts_token = await _xsts_auth(xbl_token)

        # Minecraft 认证
        self.minecraft_access_token = await _minecraft_auth(uhs, xsts_token)

        # 验证游戏所有权
        await self._verify_ownership()

        # 获取玩家信息
        await self._get_profile()

        return {
            "username": self.username,
            "uuid": self.uuid,
            "access_token": self.minecraft_access_token,
            "refresh_token": self.refresh_token
        }

    async def refresh_token(self):
        """刷新访问令牌"""
        if not self.refresh_token:
            raise Exception("No refresh token available")

        token_url = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
        payload = {
            "client_id": self.client_id,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token",
            "scope": "XboxLive.signin offline_access"
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        async with aiohttp.ClientSession() as session:
            async with session.post(token_url, data=payload, headers=headers) as resp:
                if resp.status != 200:
                    raise Exception("Token refresh failed")
                data = await resp.json()
                self.access_token = data['access_token']
                self.refresh_token = data['refresh_token']
                return self.access_token


class DownloadClass:
    def __init__(self, session):
        self.session = session

    async def download_file(self, url, dest):
        while True:
            try:
                async with self.session.get(url) as response:
                    response.raise_for_status()
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    async with aiofiles.open(dest, 'wb') as file:
                        while True:
                            chunk = await response.content.read(8192)
                            if not chunk:
                                break
                            await file.write(chunk)
                    break
            except aiohttp.ClientResponseError as e:
                if e.status == 429:
                    await asyncio.sleep(1)
                else:
                    print(f"Error downloading file: {url}, {dest}")
                    raise e

    async def download_log4j2(self, version_info, version):
        if 'logging' in version_info:
            log4j2_url = version_info['logging']['client']['file']['url']
            log4j2_path = f".minecraft/versions/{version}/log4j2.xml"
            await self.download_file(log4j2_url, log4j2_path)

    async def download_library(self, library, os_name, os_arch, version):
        artifact = library.get('downloads', {}).get('artifact')
        need_extract = False

        if not _check_rules(library, os_name):
            return

        classifiers = library.get('downloads', {}).get('classifiers')
        if classifiers:
            natives = library.get("natives")
            if natives and os_name in natives:
                native_classifier = natives[os_name].replace("${arch}", os_arch)
            else:
                native_classifier = f"natives-{os_name}"
            for native, info in classifiers.items():
                if native == native_classifier:
                    artifact = info
                    need_extract = True
                    break

        if artifact:
            library_path = f".minecraft/libraries/{artifact['path']}"
            await self.download_file(artifact['url'], library_path)
            need_extract = need_extract or "natives" in artifact['url']
            if need_extract:
                extract_path = f".minecraft/versions/{version}/{version}-natives"
                os.makedirs(extract_path, exist_ok=True)
                with zipfile.ZipFile(library_path, 'r') as zip_ref:
                    file_dict = {}
                    for member in zip_ref.namelist():
                        if member.startswith("META-INF/") or member.endswith("/") or member.endswith("LICENSE"):
                            continue
                        if (os_arch == "64" and any(x in member for x in ["86", "32", "arm"])) or (
                                os_arch == "32" and any(x in member for x in ["64", "arm"])) or (
                                os_arch == "arm64" and any(x in member for x in ["86", "32"])):
                            continue
                        extract_file_path = os.path.join(extract_path, os.path.basename(member))
                        file_dict[member] = extract_file_path

                    processed_dict = {}
                    keys_to_delete = []
                    for member, extract_file_path in file_dict.items():
                        if member.endswith("class"):
                            raise Exception("错误的natives")
                        file_name_main = re.sub(r'(x86|x64|x32|86|64|32)', '', member)
                        file_name_main = re.sub(r'[-_]', '', file_name_main)
                        file_name_main = re.sub(r'\.\w+$', '', file_name_main)
                        if file_name_main in processed_dict:
                            if len(member) > len(processed_dict[file_name_main][0]):
                                keys_to_delete.append(processed_dict[file_name_main][0])
                                processed_dict[file_name_main] = (member, extract_file_path)
                        else:
                            processed_dict[file_name_main] = (member, extract_file_path)

                    for key in keys_to_delete:
                        if key in file_dict:
                            del file_dict[key]
                    for member, extract_file_path in file_dict.items():
                        with zip_ref.open(member) as source, open(extract_file_path, "wb") as target:
                            shutil.copyfileobj(source, target)  # type:ignore

    async def download_game_files(self, version_info, version, os_name, os_arch):
        libraries = version_info.get('libraries', [])
        os.makedirs(f".minecraft/versions/{version}/{version}-natives", exist_ok=True)
        tasks = [self.download_library(library, os_name, os_arch, version) for library in libraries]
        await asyncio.gather(*tasks)

    async def download_assets(self, version_info):
        asset_index_url = version_info.get('assetIndex', {}).get('url')
        if asset_index_url:
            asset_index_id = version_info['assetIndex']['id']
            asset_index_path = f".minecraft/assets/indexes/{asset_index_id}.json"
            await self.download_file(asset_index_url, asset_index_path)

            async with aiofiles.open(asset_index_path, 'r') as file:
                asset_index = json.loads(await file.read())

            tasks = []
            for asset, info in asset_index.get('objects', {}).items():
                file_hash = info['hash']
                asset_url = f"https://resources.download.minecraft.net/{file_hash[:2]}/{file_hash}"
                asset_path = f".minecraft/assets/objects/{file_hash[:2]}/{file_hash}"
                tasks.append(self.download_file(asset_url, asset_path))
            await asyncio.gather(*tasks)

    async def download_version(self, version_info, version, os_name, os_arch):
        await asyncio.gather(self.download_game_files(version_info, version, os_name, os_arch),
                             self.download_assets(version_info),
                             self.download_file(version_info.get('downloads', {}).get('client', {}).get('url'),
                                                f".minecraft/versions/{version}/{version}.jar"),
                             self.download_log4j2(version_info, version))


# 异步获取类路径
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
    """异步查找所有Java安装路径并返回版本字典"""
    # 常量定义
    java_executables = ("javaw.exe", "java.exe")
    keywords = {"java", "jdk", "jre", "oracle", "minecraft", "runtime"}
    ignore_dirs = {"windows", "program files", "system32", "temp"}
    version_pattern = re.compile(r'"(\d+\.\d+)[._]\d+\D*"')
    scanned_paths: Set[str] = set()
    java_versions: Dict[str, str] = {}

    async def safe_scandir(path: str) -> List[os.DirEntry]:
        """类型安全的异步目录扫描"""
        try:
            entries: List[os.DirEntry] = await asyncio.to_thread(
                lambda: list(os.scandir(path))  # 显式转换为列表
            )
            return entries
        except (PermissionError, FileNotFoundError, NotADirectoryError):
            return []
        except Exception as e:
            logging.debug(f"Scan error in {path}: {str(e)}")
            return []

    async def scan_path(path: str, depth: int = 0) -> None:
        """异步递归扫描路径"""
        if depth > 4 or not os.path.isdir(path) or any(ign in path.lower() for ign in ignore_dirs):
            return

        try:
            entries = await safe_scandir(path)
            for entry in entries:
                entry_path = entry.path.replace("\\", "/")

                # 发现Java可执行文件
                if entry.is_file() and entry.name.lower() in java_executables:
                    parent_dir = os.path.dirname(entry_path)
                    if parent_dir not in scanned_paths:
                        scanned_paths.add(parent_dir)
                        version = await get_java_version(parent_dir)
                        java_versions[parent_dir] = version
                        logging.info(f"Found Java {version} at {parent_dir}")
                    continue

                # 递归扫描目录
                if entry.is_dir() and not entry.name.startswith("."):
                    dir_name = entry.name.lower()
                    if any(kw in dir_name for kw in keywords) or depth < 2:
                        await scan_path(str(entry_path), depth + 1)

        except Exception as e:
            logging.debug(f"Error processing {path}: {str(e)}")

    async def get_java_version(path: str) -> str:
        """异步获取Java版本信息"""
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

            # 新版本正则表达式
            version_match = re.search(
                r'version "(\d+)(?:\.\d+)?(?:\.[\d_]+)?(?:-[a-zA-Z0-9]+)?"',
                output
            )

            if version_match:
                major_version = version_match.group(1)
                # 处理旧版 1.x 格式
                if major_version == "1":
                    # 匹配类似 "1.8.0_381" 的情况
                    minor_match = re.search(r'"1\.(\d+)\.', output)
                    if minor_match:
                        return f"Java {minor_match.group(1)}"
                    else:
                        return "Java 8"  # 默认回退
                return f"Java {major_version}"

        except (asyncio.TimeoutError, FileNotFoundError):
            return "timeout"
        except Exception as e:
            logging.debug(f"Version check failed: {str(e)}")

        return "unknown"

    # 扫描所有可能的位置
    scan_tasks = []

    # 环境变量路径
    for env_var in ["PATH", "JAVA_HOME"]:
        if paths := os.getenv(env_var, ""):
            for path in (p.strip() for p in paths.split(os.pathsep) if p.strip()):
                abs_path = os.path.abspath(path)
                scan_tasks.append(scan_path(abs_path))

    # 磁盘分区和特殊目录
    special_paths = [
        *(p.mountpoint for p in psutil.disk_partitions() if p.fstype),
        os.getenv("APPDATA", ""),
        os.getenv("LOCALAPPDATA", ""),
        os.getcwd(),
    ]
    for path in filter(os.path.isdir, special_paths):
        scan_tasks.append(scan_path(path))

    # 并行执行所有扫描任务
    await asyncio.gather(*scan_tasks)

    return java_versions


# 异步获取启动参数
async def get_args(version_info, version):
    java = asyncio.create_task(async_find_java())
    username = "QCLTEST"
    auth_uuid = "6a058693-08f0-4404-b53f-c17bb3acea64"
    token = "6a058693-08f0-4404-b53f-c17bb3acea64"
    """
    # 全新认证
    authenticator = MinecraftAuthenticator()
    credentials = await authenticator.authenticate()
    print(f"认证成功: {credentials['username']}")

    # 使用刷新令牌
    refreshed = MinecraftAuthenticator(refresh_token=credentials['refresh_token'])
    await refreshed.refresh_token()
    await refreshed.authenticate()
    """
    os_name, os_arch = await get_os_info()
    if os_arch != "arm64":
        if os_arch == "32":
            os_arch = "86"
        os_arch = "x" + os_arch
    cp = asyncio.create_task(get_cp(version_info, version, os_name))
    game_args = ""
    java_args = ""
    main_class = version_info.get("mainClass")
    if main_class is None:
        raise ValueError(f"版本{version}没有找到mainClass")
    game_args_list = version_info.get('arguments', {}).get('game', [])
    for i in range(len(game_args_list)):
        if type(game_args_list[i]) == str:
            game_args += (" " + game_args_list[i])
        elif type(game_args_list[i]) == dict:
            pass
        else:
            raise ValueError(f"不支持的参数类型{type(game_args_list[i])},位于{version_info['arguments']['game'][i]}")
    java_args_list = version_info.get('arguments', {}).get('jvm', [])
    for i in range(len(java_args_list)):
        if type(java_args_list[i]) == str:
            java_args += (" " + java_args_list[i])
        elif type(java_args_list[i]) == dict:
            if _check_rules(java_args_list[i], os_name, os_arch):
                java_arg = java_args_list[i].get('value', '')
                java_args += java_arg
                # print(f"在java参数中{java_args_list[i]}获取到了匹配的参数:{java_arg}")
        else:
            raise ValueError(f"不支持的参数类型{type(java_args_list[i])},位于{version_info['arguments']['jvm'][i]}")
    if "-Djava.library.path=${natives_directory}" not in java_args:
        java_args += "-Djava.library.path=${natives_directory}"

    game_directory = os.path.join(os.getcwd(), ".minecraft")
    natives_directory = os.path.join(game_directory, "versions", version, f"{version}-natives")
    assets_root = os.path.join(game_directory, "assets")
    version_directory = os.path.join(game_directory, "versions", version)
    log4j_path = os.path.join(version_directory, "log4j2.xml")
    log4j_arg = version_info.get('logging', {}).get('client', {}).get('argument', '').replace("${path}", log4j_path)
    version_type = version_info.get('type', 'release')
    await cp
    cp = cp.result()
    replacements = {"${auth_player_name}": username, "${classpath}": cp, "${natives_directory}": natives_directory,
                    "${launcher_name}": "MinecraftLauncher", "${launcher_version}": "1.0",
                    "${version_name}": version,
                    "${version_type}": version_type, "${assets_root}": assets_root,
                    "${assets_index_name}": version_info.get('assets', 'legacy'), "${game_directory}": game_directory,
                    "${auth_uuid}": auth_uuid, "${auth_access_token}": token, "${user_type}": "msa"}
    main_class = version_info.get('mainClass')
    java_version = str(version_info.get('javaVersion', {}).get("majorVersion", '21'))
    java_map = await java
    print("\n检测到的Java安装：")
    java_path = ""
    for path, ver in java_map.items():
        print(f"{ver.rjust(8)} : {path}")
        if ver.replace("Java", "").replace(" ", "") == java_version:
            print(f"使用此java")
            path = os.path.join(path, "javaw.exe")
            java_path = path
            break
    if java_path == "":
        print("未找到合适的Java")
    args = f"{java_path} {java_args} {main_class} {game_args} {log4j_arg}"
    for key, value in replacements.items():
        args = args.replace(key, value)
    print(args)
    return args


def execute_javaw_blocking(
        raw_command: str,
        stdout_handler: Callable[[str], None] = lambda x: print(f"[STDOUT] {x}"),
        stderr_handler: Callable[[str], None] = lambda x: print(f"[STDERR] {x}")
) -> int:
    """
    同步执行命令并实时捕获输出（阻塞主线程）

    :return: 进程退出码
    """
    # Windows参数处理
    command = shlex.split(raw_command, posix=False)

    # 启动进程
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        text=True
    )

    # 定义流读取函数
    def stream_reader(stream, handler):
        while True:
            try:
                line = stream.readline()
                if not line:
                    break
                handler(line.rstrip())
            except Exception as e:
                print(f"流读取错误: {e}")

    # 启动输出线程
    stdout_thread = Thread(target=stream_reader, args=(process.stdout, stdout_handler))
    stderr_thread = Thread(target=stream_reader, args=(process.stderr, stderr_handler))
    stdout_thread.daemon = True
    stderr_thread.daemon = True
    stdout_thread.start()
    stderr_thread.start()

    # 阻塞等待进程结束
    process.wait()

    # 等待输出线程完成
    stdout_thread.join(timeout=1)
    stderr_thread.join(timeout=1)

    # 关闭流（重要！）
    process.stdout.close()
    process.stderr.close()

    return process.returncode
# 启动器函数
async def launcher(version_info, version):
    # username = input("请输入你的用户名:")
    # 获取启动参数
    args = await get_args(version_info, version)
    # 启动并输出程序输出，不异步
    exit_code = execute_javaw_blocking(args)
    print(f"进程退出码: {exit_code}")



# 异步获取操作系统信息
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


# 主函数
async def main():
    os_name, os_arch = await get_os_info()
    user_choice = input("请输入你想要的操作:\n1. 下载\n2. 启动\n")
    connector = aiohttp.TCPConnector(limit_per_host=25)
    async with aiohttp.ClientSession(connector=connector) as session:
        version_manifest_url = "https://piston-meta.mojang.com/mc/game/version_manifest.json"
        version_manifest_path = ".minecraft/version_manifest.json"
        downloader = DownloadClass(session)

        if user_choice == "1":
            await downloader.download_file(version_manifest_url, version_manifest_path)
            async with aiofiles.open(version_manifest_path, 'r') as file:
                version_manifest = json.loads(await file.read())

            latest_release = version_manifest['latest']['release']
            latest_snapshot = version_manifest['latest']['snapshot']
            versions = {version['id']: version['url'] for version in version_manifest['versions']}

            print(f"最新发布版本: {latest_release}")
            print(f"最新快照版本: {latest_snapshot}")

            selected_version = input("请输入要下载的版本: ")
            if selected_version not in versions:
                print("无效的版本号")
                return

            version_info_url = versions[selected_version]
            version_info_path = f".minecraft/versions/{selected_version}/{selected_version}.json"
            await downloader.download_file(version_info_url, version_info_path)

            async with aiofiles.open(version_info_path, 'r') as file:
                version_info = json.loads(await file.read())

            await downloader.download_version(version_info, selected_version, os_name, os_arch)
        elif user_choice == "2":
            versions = os.listdir(".minecraft/versions")
            version = input(f"请输入要启动的版本: {versions}\n")
            version_info_path = f".minecraft/versions/{version}/{version}.json"
            async with aiofiles.open(version_info_path, 'r') as file:
                version_info = json.loads(await file.read())
            await launcher(version_info, version)


if __name__ == "__main__":
    asyncio.run(main())
