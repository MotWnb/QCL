import asyncio
import json
import os
import subprocess
import sys
from threading import Thread
from typing import Callable, Optional

from log_manager import logger as logging

from utils import get_cp, async_find_java, get_os_info, _check_rules

# 读取配置文件
with open('config.json', 'r') as f:
    config = json.load(f)

class MinecraftLauncher:
    def __init__(self):
        pass

    @staticmethod
    async def get_args(version_info, version, version_isolation_enabled):
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

        # 新增路径计算
        original_game_directory = os.path.abspath(config['minecraft_base_dir'])
        version_directory = str(os.path.join(original_game_directory, "versions", version))
        natives_directory = os.path.join(original_game_directory, "versions", version, f"{version}-natives")

        # 根据版本隔离状态设置game_directory
        game_directory = version_directory if version_isolation_enabled else original_game_directory

        # 异步获取类路径和Java信息
        cp_task = asyncio.create_task(get_cp(version_info, version, os_name, os_arch, version_directory))

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
                logging.error(f"非法参数类型: {type(arg)}")
                raise ValueError(f"非法参数类型: {type(arg)}")
        minecraftArguments = version_info.get('minecraftArguments', '')
        if minecraftArguments:
            game_args.append(minecraftArguments)
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
                logging.error(f"非法参数类型: {type(arg)}")
                raise ValueError(f"非法参数类型: {type(arg)}")
        # 确保包含必要的JVM参数
        required_jvm_args = [
            "-XX:+UseG1GC",
            "-XX:-UseAdaptiveSizePolicy",
            "-XX:-OmitStackTraceInFastThrow",
            "-Djdk.lang.Process.allowAmbiguousCommands=true",
            "-Dfml.ignoreInvalidMinecraftCertificates=True",
            "-Dfml.ignorePatchDiscrepancies=True",
            "-Dlog4j2.formatMsgNoLookups=true",
            "-Djava.library.path=${natives_directory}",
            "-Djna.tmpdir=${natives_directory}",
            "-Dorg.lwjgl.system.SharedLibraryExtractPath=${natives_directory}",
            "-Dio.netty.native.workdir=${natives_directory}",
            "-cp ${classpath}"
        ]
        for arg in required_jvm_args:
            if arg not in java_args:
                java_args.append(arg)

        # 等待异步任务完成
        java_map = await java_task
        cp = await cp_task

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
            "${assets_root}": os.path.join(original_game_directory, "assets"),
            "${assets_index_name}": version_info.get('assets', 'legacy'),
            "${game_directory}": game_directory,
            "${auth_uuid}": auth_uuid,
            "${auth_access_token}": token,
            "${user_type}": "msa"
        }

        # 选择Java版本
        required_java_version = str(version_info.get('javaVersion', {}).get("majorVersion", "21"))
        logging.info(f"需要的Java版本: {required_java_version}")
        logging.info("检测到的Java安装：")

        java_path = ""
        for path, ver in java_map.items():
            logging.info(f"  {ver.ljust(10)} : {path}")
            # 精确版本匹配逻辑
            if ver.replace("Java", "").strip() == required_java_version:
                java_exe = "javaw.exe" if os_name == "windows" else "java"
                candidate_path = os.path.join(path, java_exe)
                if os.path.exists(candidate_path):
                    java_path = candidate_path
                    logging.info(f"使用匹配的Java: {java_path}")
                    break

        # Java未找到的容错处理
        if not java_path:
            logging.warning("警告：未找到精确匹配的Java，尝试使用最新版本")
            latest_java = max(java_map.items(), key=lambda x: x[1], default=None)
            if latest_java:
                java_path = os.path.join(latest_java[0], "javaw.exe" if os_name == "windows" else "java")
                logging.info(f"使用最新Java: {java_path}")

        # 构建最终命令
        command = [java_path]
        # 确保所有JVM参数在主类之前
        command.extend(java_args)
        # if log4j_arg.strip():
            # command.append(log4j_arg.strip())
        command.append(version_info["mainClass"])
        command.extend(game_args)

        processed_command = []
        for part in command:
            for key, value in replacements.items():
                part = part.replace(key, value)
            processed_command.append(part)

        logging.debug("最终启动命令：" + " ".join(processed_command))
        return processed_command

    @staticmethod
    def execute_javaw_blocking(
            command: list,
            stdout_handler: Callable[[str], None] = lambda x: print(f"[STDOUT] {x}"),
            stderr_handler: Callable[[str], None] = lambda x: print(f"[STDERR] {x}"),
            cwd: Optional[str] = None  # 新增参数：控制工作目录
    ) -> int:
        # 创建 QCL 目录（如果不存在）
        qcl_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "QCL")
        os.makedirs(qcl_dir, exist_ok=True)

        # 生成批处理文件路径
        bat_file_path = os.path.join(qcl_dir, "latest_start.bat")

        # 将命令写入批处理文件
        with open(bat_file_path, 'w', encoding='utf-8') as f:
            f.write("@echo off\n")
            f.write(" ".join(command))

        # 执行批处理文件
        process = subprocess.Popen(
            bat_file_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            text=True,
            cwd=cwd  # 关键修改：设置子进程的工作目录
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
        stdout_thread.join()
        stderr_thread.join()
        process.stdout.close()
        process.stderr.close()
        return process.returncode

    async def launcher(self, version_info, version, version_cwd, version_isolation_enabled):
        args = await self.get_args(version_info, version, version_isolation_enabled)
        exit_code = self.execute_javaw_blocking(args, cwd=version_cwd)
        logging.info(f"进程退出码: {exit_code}")