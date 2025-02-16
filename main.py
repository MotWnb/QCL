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
def _check_rules(library, os_name, os_arch = None):
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
                        if (os_arch == "64" and any(x in member for x in ["86", "32", "arm"])) or \
                                (os_arch == "32" and any(x in member for x in ["64", "arm"])) or \
                                (os_arch == "arm64" and any(x in member for x in ["86", "32"])):
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
                            shutil.copyfileobj(source, target) # type:ignore

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
        await asyncio.gather(
            self.download_game_files(version_info, version, os_name, os_arch),
            self.download_assets(version_info),
            self.download_file(version_info.get('downloads', {}).get('client', {}).get('url'),
                               f".minecraft/versions/{version}/{version}.jar"),
            self.download_log4j2(version_info, version)
        )

# 异步获取类路径
async def get_cp(version_info, version,os_name):
    cp = ""
    for library in version_info.get('libraries', []):
        artifact = library.get('downloads', {}).get('artifact')
        if artifact and _check_rules(library, os_name):
            cp += f".minecraft/libraries/{artifact['path']};"
        classifiers = library.get('downloads', {}).get('classifiers')
        if classifiers:
            for native in classifiers:
                cp += f".minecraft/libraries/{classifiers[native]['path']};"
    cp += f".minecraft/versions/{version}/{version}.jar"
    return cp

# 异步获取启动参数
async def get_args(version_info, version, username, token):
    os_name, os_arch = await get_os_info()
    if os_arch != "arm64":
        if os_arch == "32":
            os_arch = "86"
        os_arch = "x" + os_arch
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
                print(f"在java参数中{java_args_list[i]}获取到了匹配的参数:{java_arg}")
        else:
            raise ValueError(f"不支持的参数类型{type(java_args_list[i])},位于{version_info['arguments']['jvm'][i]}")
        if "-Djava.library.path=${natives_directory}" not in java_args:
            java_args += "-Djava.library.path=${natives_directory}"

    cp = await get_cp(version_info, version, os_name)
    game_directory = os.path.join(os.getcwd(), ".minecraft")
    natives_directory = os.path.join(game_directory, "versions", version, f"{version}-natives")
    assets_root = os.path.join(game_directory, "assets")
    version_directory = os.path.join(game_directory, "versions", version)
    log4j_path = os.path.join(version_directory, "log4j2.xml")
    log4j_arg = version_info.get('logging', {}).get('client', {}).get('argument', '').replace("${path}", log4j_path)
    replacements = {
        "${auth_player_name}": username,
        "${classpath}": cp,
        "${natives_directory}": natives_directory,
        "${launcher_name}": "Minecraft Launcher",
        "${launcher_version}": "1.0",
        "${version_name}": version,
        "${version_type}": version_info.get('type', 'release'),
        "${assets_root}": assets_root,
        "${assets_index_name}": version_info.get('assets', 'legacy'),
        "${game_directory}": game_directory,
    }
    args = f"{java_args} "
    for key, value in replacements.items():
        args = args.replace(key, value)
    return args

# 启动器函数
async def launcher(version_info, version, os_name):
    username = input("请输入你的用户名:")
    # 获取启动参数
    args = await get_args(version_info, version, username)
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
            await launcher(version_info, version, os_name)

if __name__ == "__main__":
    asyncio.run(main())