import asyncio
import os
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
        # 异步获取Java安装信息
        java_task = asyncio.create_task(async_find_java())

        # 用户认证信息（示例值）
        username = "QCLTEST"
        auth_uuid = "6a058693-08f0-4404-b53f-c17bb3acea64"
        token = "6a058693-08f0-4404-b53f-c17bb3acea64"

        # 获取操作系统信息
        os_name, raw_arch = await get_os_info()

        # 处理架构命名规范
        os_arch = f"x{raw_arch}" if raw_arch in ["86", "64"] else raw_arch  # 转换为x86/x64/arm64

        # 异步获取类路径和Java信息
        cp_task = asyncio.create_task(get_cp(version_info, version, os_name))

        # 处理游戏参数
        game_args = []
        for arg in version_info.get('arguments', {}).get('game', []):
            if isinstance(arg, str):
                game_args.append(arg)
            elif isinstance(arg, dict):
                # 处理带条件的参数（示例features配置）
                if _check_rules(arg, os_name, os_arch, features={
                    'is_demo_user': False,
                    'has_custom_resolution': False,
                    'has_quick_plays_support': False
                }):
                    value = arg.get('value', [])
                    if isinstance(value, list):
                        game_args.extend(value)
                    else:
                        game_args.append(str(value))
            else:
                raise ValueError(f"非法参数类型: {type(arg)}")

        # 处理JVM参数
        java_args = []
        for arg in version_info.get('arguments', {}).get('jvm', []):
            if isinstance(arg, str):
                java_args.append(arg)
            elif isinstance(arg, dict):
                if _check_rules(arg, os_name, os_arch):
                    value = arg.get('value', [])
                    if isinstance(value, list):
                        java_args.extend(value)
                    else:
                        java_args.append(str(value))
            else:
                raise ValueError(f"非法参数类型: {type(arg)}")

        # 确保包含必要的JVM参数
        required_jvm_args = [
            "-Djava.library.path=${natives_directory}",
            "-Djna.tmpdir=${natives_directory}",
            "-Dorg.lwjgl.system.SharedLibraryExtractPath=${natives_directory}",
            "-Dio.netty.native.workdir=${natives_directory}"
        ]
        for arg in required_jvm_args:
            if arg not in java_args:
                java_args.append(arg)

        # 等待异步任务完成
        java_map = await java_task
        cp = await cp_task

        # 构建路径变量
        game_directory = os.path.abspath(".minecraft")
        natives_directory = os.path.join(game_directory, "versions", version, f"{version}-natives")
        version_directory = os.path.join(game_directory, "versions", version)

        # 日志配置处理
        log_config = version_info.get('logging', {}).get('client', {})
        log4j_arg = log_config.get('argument', '').replace(
            "${path}",
            os.path.join(version_directory, "log4j2.xml")
        ) if log_config else ""

        # 变量替换字典
        replacements = {
            "${auth_player_name}": username,
            "${classpath}": cp,
            "${natives_directory}": natives_directory,
            "${launcher_name}": "MinecraftLauncher",
            "${launcher_version}": "1.0",
            "${version_name}": version,
            "${version_type}": version_info.get('type', 'release'),
            "${assets_root}": os.path.join(game_directory, "assets"),
            "${assets_index_name}": version_info.get('assets', 'legacy'),
            "${game_directory}": game_directory,
            "${auth_uuid}": auth_uuid,
            "${auth_access_token}": token,
            "${user_type}": "msa"
        }

        # 选择Java版本
        required_java_version = str(version_info.get('javaVersion', {}).get("majorVersion", "21"))
        print(f"\n需要的Java版本: {required_java_version}")
        print("检测到的Java安装：")

        java_path = ""
        for path, ver in java_map.items():
            print(f"  {ver.ljust(10)} : {path}")
            # 精确版本匹配逻辑
            if ver.replace("Java", "").strip() == required_java_version:
                java_exe = "javaw.exe" if os_name == "windows" else "java"
                candidate_path = os.path.join(path, java_exe)
                if os.path.exists(candidate_path):
                    java_path = candidate_path
                    print(f"√ 使用匹配的Java: {java_path}")
                    break

        # Java未找到的容错处理
        if not java_path:
            print("⚠ 警告：未找到精确匹配的Java，尝试使用最新版本")
            latest_java = max(java_map.items(), key=lambda x: x[1], default=None)
            if latest_java:
                java_path = os.path.join(latest_java[0], "javaw.exe" if os_name == "windows" else "java")
                print(f"使用最新Java: {java_path}")

        # 构建最终命令
        command = [java_path]
        command.extend(java_args)
        command.append(version_info["mainClass"])
        command.extend(game_args)
        if log4j_arg.strip():
            command.append(log4j_arg.strip())

        processed_command = []
        for part in command:
            for key, value in replacements.items():
                part = part.replace(key, value)
            processed_command.append(part)

        print("\n最终启动命令：")
        print(" ".join(processed_command))
        return processed_command

    @staticmethod
    def execute_javaw_blocking(command: list,
                               stdout_handler: Callable[[str], None] = lambda x: print(f"[STDOUT] {x}"),
                               stderr_handler: Callable[[str], None] = lambda x: print(f"[STDERR] {x}")
                               ) -> int:
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
