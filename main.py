import os
import json
import platform
import re
import shutil
import aiohttp
import aiofiles
import asyncio
import zipfile


class DownloadClass:
    def __init__(self, session):
        self.session = session

    async def download_file(self, url, dest):
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
        except aiohttp.ClientResponseError as e:
            if e.status == 429:
                await asyncio.sleep(1)
                await self.download_file(url, dest)
            else:
                print(f"Error downloading file: {url}, {dest}")
                raise e

    async def download_log4j2(self, version_info):
        if 'logging' not in version_info:
            log4j2_url = version_info['logging']['client']['file']['url']
            log4j2_path = ".minecraft/log_configs/log4j2.xml"
            await self.download_file(log4j2_url, log4j2_path)

    async def download_library(self, library, os_name, os_arch, version):
        need_extract = False
        artifact = None
        if 'downloads' in library and 'artifact' in library['downloads']:
            artifact = library['downloads']['artifact']

        if "rules" in library:
            for rule in library["rules"]:  # 遍历规则
                if "action" in rule:  # 如果规则中有action字段
                    if rule["action"] == "allow":  # 如果规则是允许
                        if "os" in rule:  # 如果规则中有os字段
                            if rule["os"]["name"] == os_name:  # 当前操作系统-允许
                                continue
                            else:  # 当前操作系统-不允许
                                return  # 直接返回，不再继续
                        else:  # 没有os字段，允许
                            continue
                    elif rule["action"] == "disallow":  # 如果规则是拒绝
                        if "os" in rule:  # 如果规则中有os字段
                            if rule["os"]["name"] == os_name:  # 当前操作系统-拒绝
                                return  # 直接返回，不再继续
                            else:  # 非当前操作系统-未知
                                continue
                        else:  # 没有os字段-未知
                            continue
                    else:
                        raise ValueError(f"依赖库{library}的规则中action字段不合法")
                else:
                    raise ValueError(f"依赖库{library}的规则中缺少action字段")

        if "classifiers" in library["downloads"]:
            classifier = library["downloads"]["classifiers"]
            for native in classifier:
                if native == f"natives-{os_name}":
                    artifact = classifier[native]
                    need_extract = True
        if artifact:
            library_path = f".minecraft/libraries/{artifact['path']}"
            await self.download_file(artifact['url'], library_path)
            if "native" in artifact['url']:
                need_extract = True
            if need_extract:
                with zipfile.ZipFile(library_path, 'r') as zip_ref:
                    extract_path = f".minecraft/versions/{version}/{version}-natives"
                    os.makedirs(extract_path, exist_ok=True)
                    file_dict = {}
                    processed_dict = {}
                    for member in zip_ref.namelist():
                        # 跳过 META-INF 目录和空目录
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
                        # 使用 with 语句确保文件正确关闭
                        # with zip_ref.open(member) as source, open(extract_file_path, "wb") as target:
                        #     shutil.copyfileobj(source, target)  # type: ignore

                    # 要删除的键的列表
                    keys_to_delete = []

                    for member, extract_file_path in file_dict.items():
                        # 获取当前的文件名称，去除不需要的字符和后缀

                        file_name_main = re.sub(r'(x86|x64|x32|86|64|32)', '', member)
                        file_name_main = re.sub(r'[-_]', '', file_name_main)
                        file_name_main = re.sub(r'\.\w+$', '', file_name_main)
                        # 仅匹配连续的"86"、"64"、"32"、"x86"、"x64"或"x32"
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

                        with zip_ref.open(member) as source, open(extract_file_path, "wb") as target:
                            shutil.copyfileobj(source, target)  # type: ignore

                            # BYD类型检查器硬控我半小时


        else:
            pass
            # raise ValueError(f"依赖库{library}的下载地址或本地路径不合法")
    async def download_game_files(self, version_info, version, os_name, os_arch):

        libraries = version_info['libraries']
        tasks = []
        os.makedirs(f".minecraft/versions/{version}/{version}-natives", exist_ok=True)
        for library in libraries:
            tasks.append(self.download_library(library, os_name, os_arch, version))
        await asyncio.gather(*tasks)

    async def download_assets(self, version_info):
        asset_index_url = version_info['assetIndex']['url']
        asset_index_path = f".minecraft/assets/indexes/{version_info['assetIndex']['id']}.json"
        await self.download_file(asset_index_url, asset_index_path)

        async with aiofiles.open(asset_index_path, 'r') as file:
            asset_index = json.loads(await file.read())

        tasks = []
        for asset, info in asset_index['objects'].items():
            file_hash = info['hash']
            asset_url = f"https://resources.download.minecraft.net/{file_hash[:2]}/{file_hash}"
            asset_path = f".minecraft/assets/objects/{file_hash[:2]}/{file_hash}"
            tasks.append(self.download_file(asset_url, asset_path))
        await asyncio.gather(*tasks)

    async def download_version(self, version_info, version, os_name, os_arch):
        await asyncio.gather(
            self.download_game_files(version_info, version, os_name, os_arch),
            self.download_assets(version_info),
            self.download_file(version_info['downloads']['client']['url'],
                               f".minecraft/versions/{version}/{version}.jar"),
            self.download_log4j2(version_info)
        )


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


def launcher(version_info, version, os_name):
    java_version = None
    if 'javaVersion' in version_info:
        if 'majorVersion' in version_info['javaVersion']:
            java_version = version_info['javaVersion']['majorVersion']
    if not java_version:
        raise ValueError(f"版本{version}没有指定Java版本")


async def get_os_info():
    os_name = platform.system()
    if os_name == "Windows":
        os_name = "windows"
    elif os_name == "Linux":
        os_name = "linux"
    elif os_name == "Darwin":
        os_name = "osx"
    else:
        raise ValueError(f"不支持的操作系统{os_name}")

    os_arch = platform.architecture()[0]
    if os_arch == "64bit":
        os_arch = "64"
    elif os_arch == "32bit":
        os_arch = "86"
    elif os_arch == "arm64":
        os_arch = "arm64"

    return os_name, os_arch


async def main():
    os_name, os_arch = await get_os_info()
    user_choice = input("请输入你想要的操作:\n1. 下载\n2. 启动\n")
    connector = aiohttp.TCPConnector(limit_per_host=25)
    async with aiohttp.ClientSession(connector=connector) as session:
        version_manifest_url = "https://piston-meta.mojang.com/mc/game/version_manifest.json"
        version_manifest_path = ".minecraft/version_manifest.json"
        if user_choice == "1":
            await DownloadClass(session).download_file(version_manifest_url, version_manifest_path)

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

            await DownloadClass(session).download_file(version_info_url, version_info_path)

            async with aiofiles.open(version_info_path, 'r') as file:
                version_info = json.loads(await file.read())

            downloader = DownloadClass(session)
            await downloader.download_version(version_info, selected_version, os_name, os_arch)
        elif user_choice == "2":
            versions = os.listdir(".minecraft/versions")
            version = input("请输入要启动的版本: " + str(versions) + "\n")
            version_info_path = f".minecraft/versions/{version}/{version}.json"
            async with aiofiles.open(version_info_path, 'r') as file:
                version_info = json.loads(await file.read())
            launcher(version_info, version, os_name)


if __name__ == "__main__":
    asyncio.run(main())
