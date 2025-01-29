import os
import json
import platform
import re
import shutil
import aiohttp
import aiofiles
import asyncio
import zipfile

# 定义下载类，封装下载相关操作
class DownloadClass:
    def __init__(self, session):
        # 初始化会话
        self.session = session

    async def download_file(self, url, dest):
        try:
            # 发起 HTTP 请求获取文件
            async with self.session.get(url) as response:
                # 检查响应状态码
                response.raise_for_status()
                # 创建目标文件所在目录
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                # 以二进制写入模式打开目标文件
                async with aiofiles.open(dest, 'wb') as file:
                    while True:
                        # 每次读取 8192 字节数据
                        chunk = await response.content.read(8192)
                        if not chunk:
                            break
                        # 将读取的数据写入文件
                        await file.write(chunk)
        except aiohttp.ClientResponseError as e:
            # 如果状态码为 429，等待 1 秒后重试
            if e.status == 429:
                await asyncio.sleep(1)
                await self.download_file(url, dest)
            else:
                print(f"Error downloading file: {url}, {dest}")
                raise e

    async def download_log4j2(self, version_info):
        # 检查版本信息中是否有 logging 字段
        if 'logging' not in version_info:
            # 获取 log4j2 文件的下载链接
            log4j2_url = version_info['logging']['client']['file']['url']
            # 定义 log4j2 文件的本地路径
            log4j2_path = ".minecraft/log_configs/log4j2.xml"
            # 下载 log4j2 文件
            await self.download_file(log4j2_url, log4j2_path)

    async def download_library(self, library, os_name, os_arch, version):
        # 标记是否需要解压
        need_extract = False
        # 初始化工件信息
        artifact = None
        # 检查库信息中是否有 downloads 和 artifact 字段
        if 'downloads' in library and 'artifact' in library['downloads']:
            artifact = library['downloads']['artifact']

        # 处理库的规则
        if "rules" in library:
            for rule in library["rules"]:
                if "action" in rule:
                    if rule["action"] == "allow":
                        if "os" in rule:
                            if rule["os"]["name"] == os_name:
                                continue
                            else:
                                return
                        else:
                            continue
                    elif rule["action"] == "disallow":
                        if "os" in rule:
                            if rule["os"]["name"] == os_name:
                                return
                            else:
                                continue
                        else:
                            continue
                    else:
                        raise ValueError(f"依赖库{library}的规则中action字段不合法")
                else:
                    raise ValueError(f"依赖库{library}的规则中缺少action字段")

        # 处理分类器
        if "classifiers" in library["downloads"]:
            classifier = library["downloads"]["classifiers"]
            for native in classifier:
                if native == f"natives-{os_name}":
                    artifact = classifier[native]
                    need_extract = True

        if artifact:
            # 定义库文件的本地路径
            library_path = f".minecraft/libraries/{artifact['path']}"
            # 下载库文件
            await self.download_file(artifact['url'], library_path)
            if "native" in artifact['url']:
                need_extract = True
            if need_extract:
                # 打开压缩文件
                with zipfile.ZipFile(library_path, 'r') as zip_ref:
                    # 定义解压路径
                    extract_path = f".minecraft/versions/{version}/{version}-natives"
                    # 创建解压目录
                    os.makedirs(extract_path, exist_ok=True)
                    # 存储文件信息的字典
                    file_dict = {}
                    # 存储处理后文件名的字典
                    processed_dict = {}
                    for member in zip_ref.namelist():
                        # 跳过 META - INF 目录和空目录
                        if member.startswith("META-INF/") or member.endswith("/"):
                            continue
                        # 过滤不符合架构的文件
                        if (os_arch == "64" and any(x in member for x in ["86", "32", "arm"])) or \
                                (os_arch == "86" and any(x in member for x in ["64", "arm"])) or \
                                (os_arch == "arm64" and any(x in member for x in ["86", "32"])):
                            continue
                        # 构建提取文件的路径
                        extract_file_path = os.path.join(extract_path, os.path.basename(member))
                        file_dict[member] = extract_file_path

                    # 要删除的键的列表
                    keys_to_delete = []

                    for member, extract_file_path in file_dict.items():
                        if member.endswith("class"):
                            print("错误的natives" + extract_file_path)
                        # 获取当前的文件名称，去除不需要的字符和后缀
                        file_name_main = re.sub(r'(x86|x64|x32|86|64|32)', '', member)
                        file_name_main = re.sub(r'[-_]', '', file_name_main)
                        file_name_main = re.sub(r'\.\w+$', '', file_name_main)
                        print(f"{member} -> {file_name_main}")
                        # 检查处理后的文件名是否已经存在于processed_dict中
                        if file_name_main in processed_dict:
                            # 比较原名称的长度，保留更长的那个
                            if len(member) > len(processed_dict[file_name_main][0]):
                                print(f"存在冲突,删除短名称的键: {processed_dict[file_name_main][0]}")
                                # 记录要删除的短名称的键
                                keys_to_delete.append(processed_dict[file_name_main][0])
                                # 更新processed_dict
                                processed_dict[file_name_main] = (member, extract_file_path)
                        else:
                            # 如果处理后的文件名不存在于processed_dict中，直接添加
                            processed_dict[file_name_main] = (member, extract_file_path)

                    # 删除记录的键
                    for key in keys_to_delete:
                        if key in file_dict:
                            del file_dict[key]
                    for member, extract_file_path in file_dict.items():
                        # 解压文件
                        with zip_ref.open(member) as source, open(extract_file_path, "wb") as target:
                            shutil.copyfileobj(source, target) # type:ignore
        else:
            pass

    async def download_game_files(self, version_info, version, os_name, os_arch):
        # 获取库信息
        libraries = version_info['libraries']
        # 存储下载任务的列表
        tasks = []
        # 创建版本的 natives 目录
        os.makedirs(f".minecraft/versions/{version}/{version}-natives", exist_ok=True)
        for library in libraries:
            # 添加下载库文件的任务
            tasks.append(self.download_library(library, os_name, os_arch, version))
        # 并发执行所有任务
        await asyncio.gather(*tasks)

    async def download_assets(self, version_info):
        # 获取资产索引的下载链接
        asset_index_url = version_info['assetIndex']['url']
        # 定义资产索引的本地路径
        asset_index_path = f".minecraft/assets/indexes/{version_info['assetIndex']['id']}.json"
        # 下载资产索引文件
        await self.download_file(asset_index_url, asset_index_path)

        # 读取资产索引文件
        async with aiofiles.open(asset_index_path, 'r') as file:
            asset_index = json.loads(await file.read())

        # 存储下载资产的任务列表
        tasks = []
        for asset, info in asset_index['objects'].items():
            # 获取文件哈希值
            file_hash = info['hash']
            # 构建资产的下载链接
            asset_url = f"https://resources.download.minecraft.net/{file_hash[:2]}/{file_hash}"
            # 定义资产的本地路径
            asset_path = f".minecraft/assets/objects/{file_hash[:2]}/{file_hash}"
            # 添加下载资产的任务
            tasks.append(self.download_file(asset_url, asset_path))
        # 并发执行所有任务
        await asyncio.gather(*tasks)

    async def download_version(self, version_info, version, os_name, os_arch):
        # 并发执行下载游戏文件、资产、客户端 JAR 文件和 log4j2 文件的任务
        await asyncio.gather(
            self.download_game_files(version_info, version, os_name, os_arch),
            self.download_assets(version_info),
            self.download_file(version_info['downloads']['client']['url'],
                               f".minecraft/versions/{version}/{version}.jar"),
            self.download_log4j2(version_info)
        )

# 异步获取类路径
async def get_cp(version_info, os_name):
    cp = ""
    if 'libraries' in version_info:
        for library in version_info['libraries']:
            if 'downloads' in library and 'artifact' in library['downloads']:
                if 'rules' in library:
                    rule = library['rules'][0]
                    if 'os' in rule and rule['os']['name'] != os_name:
                        continue
                    if 'action' in rule and rule['action'] == 'disallow':
                        continue

                artifact = library['downloads']['artifact']
                cp += f".minecraft/libraries/{artifact['path']};"
            elif 'downloads' in library and 'classifiers' in library['downloads']:
                classifier = library['downloads']['classifiers']
                for native in classifier:
                    cp += f".minecraft/libraries/{classifier[native]['path']};"
    return cp

# 启动器函数
def launcher(version_info, version, os_name):
    java_version = None
    if 'javaVersion' in version_info:
        if 'majorVersion' in version_info['javaVersion']:
            java_version = version_info['javaVersion']['majorVersion']
    if not java_version:
        raise ValueError(f"版本{version}没有指定Java版本")

# 异步获取操作系统信息
async def get_os_info():
    # 获取操作系统名称
    os_name = platform.system()
    if os_name == "Windows":
        os_name = "windows"
    elif os_name == "Linux":
        os_name = "linux"
    elif os_name == "Darwin":
        os_name = "osx"
    else:
        raise ValueError(f"不支持的操作系统{os_name}")

    # 获取操作系统架构
    os_arch = platform.architecture()[0]
    if os_arch == "64bit":
        os_arch = "64"
    elif os_arch == "32bit":
        os_arch = "86"
    elif os_arch == "arm64":
        os_arch = "arm64"

    return os_name, os_arch

# 主函数
async def main():
    # 获取操作系统信息
    os_name, os_arch = await get_os_info()
    # 获取用户选择
    user_choice = input("请输入你想要的操作:\n1. 下载\n2. 启动\n")
    # 创建 TCP 连接器
    connector = aiohttp.TCPConnector(limit_per_host=25)
    # 创建异步会话
    async with aiohttp.ClientSession(connector=connector) as session:
        # 版本清单的下载链接
        version_manifest_url = "https://piston-meta.mojang.com/mc/game/version_manifest.json"
        # 版本清单的本地路径
        version_manifest_path = ".minecraft/version_manifest.json"
        if user_choice == "1":
            # 下载版本清单文件
            await DownloadClass(session).download_file(version_manifest_url, version_manifest_path)

            # 读取版本清单文件
            async with aiofiles.open(version_manifest_path, 'r') as file:
                version_manifest = json.loads(await file.read())

            # 获取最新发布版本
            latest_release = version_manifest['latest']['release']
            # 获取最新快照版本
            latest_snapshot = version_manifest['latest']['snapshot']
            # 存储版本信息的字典
            versions = {version['id']: version['url'] for version in version_manifest['versions']}

            print(f"最新发布版本: {latest_release}")
            print(f"最新快照版本: {latest_snapshot}")

            # 获取用户选择的版本
            selected_version = input("请输入要下载的版本: ")
            if selected_version not in versions:
                print("无效的版本号")
                return

            # 获取所选版本的信息链接
            version_info_url = versions[selected_version]
            # 定义所选版本信息的本地路径
            version_info_path = f".minecraft/versions/{selected_version}/{selected_version}.json"

            # 下载所选版本的信息文件
            await DownloadClass(session).download_file(version_info_url, version_info_path)

            # 读取所选版本的信息文件
            async with aiofiles.open(version_info_path, 'r') as file:
                version_info = json.loads(await file.read())

            # 创建下载器实例
            downloader = DownloadClass(session)
            # 下载所选版本的所有文件
            await downloader.download_version(version_info, selected_version, os_name, os_arch)
        elif user_choice == "2":
            # 获取已下载的版本列表
            versions = os.listdir(".minecraft/versions")
            # 获取用户要启动的版本
            version = input("请输入要启动的版本: " + str(versions) + "\n")
            # 定义所选版本信息的本地路径
            version_info_path = f".minecraft/versions/{version}/{version}.json"
            # 读取所选版本的信息文件
            async with aiofiles.open(version_info_path, 'r') as file:
                version_info = json.loads(await file.read())
            # 启动所选版本
            launcher(version_info, version, os_name)

if __name__ == "__main__":
    # 运行主函数
    asyncio.run(main())