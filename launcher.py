import asyncio
import os
import shlex
import subprocess
import sys
from threading import Thread
from typing import Callable

from utils import get_cp, async_find_java, get_os_info, _check_rules


class MinecraftLauncher:
    def __init__(self):
        pass

    @staticmethod
    async def get_args(version_info, version):
        java = asyncio.create_task(async_find_java())
        username = "QCLTEST"
        auth_uuid = "6a058693-08f0-4404-b53f-c17bb3acea64"
        token = "6a058693-08f0-4404-b53f-c17bb3acea64"
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
        for arg in game_args_list:
            if isinstance(arg, str):
                game_args += f" {arg}"
            elif isinstance(arg, dict):
                pass
            else:
                raise ValueError(f"不支持的参数类型{type(arg)},位于{arg}")
        java_args_list = version_info.get('arguments', {}).get('jvm', [])
        for arg in java_args_list:
            if isinstance(arg, str):
                java_args += f" {arg}"
            elif isinstance(arg, dict):
                if _check_rules(arg, os_name, os_arch):
                    java_arg = arg.get('value', '')
                    java_args += java_arg
            else:
                raise ValueError(f"不支持的参数类型{type(arg)},位于{arg}")
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
                        "${assets_index_name}": version_info.get('assets', 'legacy'),
                        "${game_directory}": game_directory,
                        "${auth_uuid}": auth_uuid, "${auth_access_token}": token, "${user_type}": "msa"}
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

    @staticmethod
    def execute_javaw_blocking(raw_command: str,
                               stdout_handler: Callable[[str], None] = lambda x: print(f"[STDOUT] {x}"),
                               stderr_handler: Callable[[str], None] = lambda x: print(f"[STDERR] {x}")
                               ) -> int:
        command = shlex.split(raw_command, posix=False)
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            text=True
        )

        def stream_reader(stream, handler):
            while True:
                try:
                    line = stream.readline()
                    if not line:
                        break
                    handler(line.rstrip())
                except Exception as e:
                    print(f"流读取错误: {e}")

        stdout_thread = Thread(target=stream_reader, args=(process.stdout, stdout_handler))
        stderr_thread = Thread(target=stream_reader, args=(process.stderr, stderr_handler))
        stdout_thread.daemon = True
        stderr_thread.daemon = True
        stdout_thread.start()
        stderr_thread.start()

        process.wait()
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)
        process.stdout.close()
        process.stderr.close()
        return process.returncode

    async def launcher(self, version_info, version):
        args = await self.get_args(version_info, version)
        exit_code = self.execute_javaw_blocking(args)
        print(f"进程退出码: {exit_code}")
