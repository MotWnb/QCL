import asyncio
import json
import logging
import os
import webbrowser
from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, Optional, Tuple, Any, List

import aiofiles
import aiohttp
import pyperclip

logger = logging.getLogger('QCL')


class AuthMethod(Enum):
    """验证方式枚举"""
    MICROSOFT = 1
    OFFLINE = 2
    THIRD_PARTY = 3


class IAuthenticator(ABC):
    """验证器接口"""

    @abstractmethod
    async def authenticate(self, refresh_token: Optional[str] = None) -> Dict:
        """执行验证流程并返回验证结果"""
        pass


class MicrosoftAuthenticator(IAuthenticator):
    """微软账号正版验证器"""

    def __init__(self, client_id: str = "de243363-2e6a-44dc-82cb-ea8d6b5cd98d",
                 redirect_uri: str = "http://localhost:8080/callback"):
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.microsoft_auth_endpoint = "https://login.microsoftonline.com/consumers/oauth2/v2.0"
        self.xbox_auth_endpoint = "https://user.auth.xboxlive.com/user/authenticate"
        self.xsts_auth_endpoint = "https://xsts.auth.xboxlive.com/xsts/authorize"
        self.minecraft_auth_endpoint = "https://api.minecraftservices.com/authentication/login_with_xbox"
        self.minecraft_entitlements_endpoint = "https://api.minecraftservices.com/entitlements/mcstore"
        self.minecraft_profile_endpoint = "https://api.minecraftservices.com/minecraft/profile"

    async def authenticate(self, refresh_token: Optional[str] = None) -> Dict:
        """执行完整的微软账号验证流程"""
        try:
            if refresh_token:
                # 使用刷新令牌获取新的访问令牌
                logger.info("使用刷新令牌进行验证...")
                microsoft_token = await self._refresh_microsoft_token(refresh_token)
            else:
                # 使用设备代码流进行验证
                logger.info("使用设备代码流进行验证...")
                microsoft_token = await self._device_code_flow()

            # Xbox Live 身份验证
            xbl_token, xbl_uhs = await self._authenticate_with_xbox_live(microsoft_token["access_token"])

            # XSTS 身份验证
            xsts_token, xsts_uhs = await self._authenticate_with_xsts(xbl_token)

            # Minecraft 身份验证
            minecraft_token = await self._authenticate_with_minecraft(xsts_uhs, xsts_token)

            # 检查游戏拥有情况
            has_game = await self._check_game_ownership(minecraft_token["access_token"])
            if not has_game:
                raise Exception("该账号没有购买 Minecraft")

            # 获取玩家 UUID 和用户名
            profile = await self._get_minecraft_profile(minecraft_token["access_token"])

            # 合并所有必要的验证信息
            result = {
                "username": profile["name"],
                "uuid": profile["id"],
                "access_token": minecraft_token["access_token"],
                "refresh_token": microsoft_token.get("refresh_token", ""),
                "skins": profile.get("skins", []),
                "capes": profile.get("capes", [])
            }

            logger.info(f"验证成功: {result['username']} ({result['uuid']})")
            return result

        except Exception as e:
            logger.error(f"验证失败: {str(e)}")
            raise

    async def _device_code_flow(self) -> Dict:
        """执行设备代码流验证"""
        # 获取设备代码和用户代码
        device_code_data = await self._get_device_code()

        # 显示用户代码并打开验证页面
        print(f"请打开 {device_code_data['verification_uri']}")
        print(f"并输入代码: {device_code_data['user_code']}")
        print("代码已复制到剪贴板")

        # 复制代码到剪贴板并打开浏览器
        pyperclip.copy(device_code_data['user_code'])
        webbrowser.open(device_code_data['verification_uri'])

        # 轮询用户授权状态
        return await self._poll_for_authorization(
            device_code_data['device_code'],
            device_code_data['interval']
        )

    async def _get_device_code(self) -> Dict:
        """获取设备代码和用户代码"""
        url = f"{self.microsoft_auth_endpoint}/devicecode"
        data = {
            "client_id": self.client_id,
            "scope": "XboxLive.signin offline_access"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=data) as response:
                response.raise_for_status()
                return await response.json()

    async def _poll_for_authorization(self, device_code: str, interval: int) -> Any | None:
        """轮询用户授权状态"""
        url = f"{self.microsoft_auth_endpoint}/token"
        data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": self.client_id,
            "device_code": device_code
        }

        while True:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=data) as response:
                    if response.status == 400:
                        error_data = await response.json()
                        error = error_data.get("error")

                        if error == "authorization_pending":
                            # 用户尚未完成授权，继续等待
                            await asyncio.sleep(interval)
                            continue
                        elif error == "slow_down":
                            # 请求过于频繁，增加等待时间
                            await asyncio.sleep(interval * 2)
                            continue
                        else:
                            # 其他错误，终止验证
                            raise Exception(f"授权失败: {error} - {error_data.get('error_description')}")
                    else:
                        # 授权成功
                        response.raise_for_status()
                        return await response.json()
        return None

    async def _refresh_microsoft_token(self, refresh_token: str) -> Dict:
        """使用刷新令牌获取新的访问令牌"""
        url = f"{self.microsoft_auth_endpoint}/token"
        data = {
            "client_id": self.client_id,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
            "scope": "XboxLive.signin offline_access"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=data) as response:
                response.raise_for_status()
                return await response.json()

    async def _authenticate_with_xbox_live(self, microsoft_token: str) -> Tuple[str, str]:
        """使用微软访问令牌进行 Xbox Live 身份验证"""
        url = self.xbox_auth_endpoint
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        payload = {
            "Properties": {
                "AuthMethod": "RPS",
                "SiteName": "user.auth.xboxlive.com",
                "RpsTicket": f"d={microsoft_token}"
            },
            "RelyingParty": "http://auth.xboxlive.com",
            "TokenType": "JWT"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as response:
                response.raise_for_status()
                data = await response.json()
                return data["Token"], data["DisplayClaims"]["xui"][0]["uhs"]

    async def _authenticate_with_xsts(self, xbl_token: str) -> Tuple[str, str]:
        """使用 XBL 令牌进行 XSTS 身份验证"""
        url = self.xsts_auth_endpoint
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
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 401:
                    # XBL 令牌无效或已过期
                    data = await response.json()
                    raise Exception(f"XSTS 验证失败: {data.get('XErr')} - {data.get('Message')}")

                response.raise_for_status()
                data = await response.json()
                return data["Token"], data["DisplayClaims"]["xui"][0]["uhs"]

    async def _authenticate_with_minecraft(self, uhs: str, xsts_token: str) -> Dict:
        """使用 XSTS 令牌进行 Minecraft 身份验证"""
        url = self.minecraft_auth_endpoint
        headers = {
            "Content-Type": "application/json"
        }
        payload = {
            "identityToken": f"XBL3.0 x={uhs};{xsts_token}"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as response:
                response.raise_for_status()
                return await response.json()

    async def _check_game_ownership(self, access_token: str) -> bool:
        """检查账号是否拥有 Minecraft"""
        url = self.minecraft_entitlements_endpoint
        headers = {
            "Authorization": f"Bearer {access_token}"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                response.raise_for_status()
                data = await response.json()
                items = data.get("items", [])
                return any(item.get("name") == "game_minecraft" for item in items)

    async def _get_minecraft_profile(self, access_token: str) -> Dict:
        """获取 Minecraft 玩家档案"""
        url = self.minecraft_profile_endpoint
        headers = {
            "Authorization": f"Bearer {access_token}"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                response.raise_for_status()
                return await response.json()


class OfflineAuthenticator(IAuthenticator):
    """离线验证器"""

    async def authenticate(self, refresh_token: Optional[str] = None) -> Dict:
        """执行离线验证流程"""
        print("使用离线验证...")
        return {
            "username": "Player",
            "uuid": "00000000-0000-0000-0000-000000000000",
            "access_token": "offline_token",
            "refresh_token": "",
            "skins": [],
            "capes": []
        }


class ThirdPartyAuthenticator(IAuthenticator):
    """第三方验证器"""

    async def authenticate(self, refresh_token: Optional[str] = None) -> Dict:
        """执行第三方验证流程"""
        print("使用第三方验证...")
        return {
            "username": "ThirdPartyPlayer",
            "uuid": "11111111-1111-1111-1111-111111111111",
            "access_token": "third_party_token",
            "refresh_token": "",
            "skins": [],
            "capes": []
        }


class AuthManager:
    """验证管理器"""

    def __init__(self):
        self._authenticators = {
            AuthMethod.MICROSOFT: MicrosoftAuthenticator(),
            AuthMethod.OFFLINE: OfflineAuthenticator(),
            AuthMethod.THIRD_PARTY: ThirdPartyAuthenticator()
        }

    async def authenticate(self, method: AuthMethod, refresh_token: Optional[str] = None) -> Dict:
        """根据指定的验证方式执行验证"""
        if method not in self._authenticators:
            raise ValueError(f"不支持的验证方式: {method}")

        authenticator = self._authenticators[method]
        return await authenticator.authenticate(refresh_token)

    @staticmethod
    async def prompt_for_auth_method() -> AuthMethod:
        """提示用户选择验证方式"""
        print("\n请选择验证方式:")
        print("1. 微软正版验证")
        print("2. 离线验证")
        print("3. 第三方验证")

        while True:
            choice = input("请输入数字选择 (1-3): ").strip()

            if choice == "1":
                return AuthMethod.MICROSOFT
            elif choice == "2":
                return AuthMethod.OFFLINE
            elif choice == "3":
                return AuthMethod.THIRD_PARTY
            else:
                print("无效的选择，请重新输入")


# 异步验证函数，根据用户选择调用对应的验证器
async def perform_authentication(refresh_token: Optional[str] = None) -> Dict:
    user_manager = UserManager("QCL/users.json")
    """根据用户选择执行异步验证并返回结果"""
    manager = AuthManager()
    auth_method = await manager.prompt_for_auth_method()
    auth_result = await manager.authenticate(auth_method, refresh_token)

    await user_manager.user_save(auth_result["uuid"], auth_result)
    return auth_result



# 处理用户的数据json保存以及加解密处理
class UserManager:
    """用户账户管理器，用于异步保存、读取和刷新用户账户数据"""

    def __init__(self, data_file: str = "users.json"):
        """
        初始化用户管理器

        Args:
            data_file: 保存用户数据的JSON文件路径
        """
        self.data_file = data_file
        self.users = {}  # 内存中的用户数据缓存

    async def user_load(self) -> Dict[str, Dict]:
        """
        异步加载所有用户账户信息

        Returns:
            包含所有用户数据的字典，键为UUID，值为用户信息
        """
        try:
            if os.path.exists(self.data_file):
                async with aiofiles.open(self.data_file, 'r', encoding='utf-8') as file:
                    content = await file.read()
                    self.users = json.loads(content)
                    logger.info(f"成功加载 {len(self.users)} 个用户数据")
            else:
                logger.info("用户数据文件不存在，创建空数据")
                self.users = {}

            return self.users

        except Exception as e:
            logger.error(f"加载用户数据失败: {str(e)}")
            self.users = {}
            return self.users

    async def user_save(self, uuid: str, user_data: Dict) -> None:
        """
        异步保存用户数据

        Args:
            uuid: 用户的唯一标识符
            user_data: 包含用户所有信息的字典
        """
        if not uuid:
            raise ValueError("UUID不能为空")

        # 确保包含必要的用户信息
        required_fields = ["username", "access_token", "refresh_token"]
        for field in required_fields:
            if field not in user_data:
                raise ValueError(f"用户数据缺少必要字段: {field}")

        # 保存用户数据，使用UUID作为键
        self.users[uuid] = user_data
        await self._save_to_file()
        logger.info(f"用户数据已保存: {user_data['username']} ({uuid})")

    async def _save_to_file(self) -> None:
        """异步将用户数据保存到文件"""
        try:
            async with aiofiles.open(self.data_file, 'w', encoding='utf-8') as file:
                await file.write(json.dumps(self.users, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.error(f"保存用户数据失败: {str(e)}")

    def user_get(self, uuid: str) -> Optional[Dict]:
        """
        根据UUID获取用户数据（同步方法，因为数据已在内存中）

        Args:
            uuid: 用户的唯一标识符

        Returns:
            用户数据字典，如果不存在则返回None
        """
        return self.users.get(uuid)

    async def user_delete(self, uuid: str) -> bool:
        """
        异步根据UUID删除用户数据

        Args:
            uuid: 用户的唯一标识符

        Returns:
            如果删除成功返回True，否则返回False
        """
        if uuid in self.users:
            del self.users[uuid]
            await self._save_to_file()
            logger.info(f"用户数据已删除: {uuid}")
            return True
        return False

    def list_all_users(self) -> List[Dict]:
        """获取所有用户数据列表（同步方法，因为数据已在内存中）"""
        return list(self.users.values())

    async def refresh_all_users(self, auth_manager) -> None:
        """
        异步刷新所有用户的账户信息

        Args:
            auth_manager: 用于刷新验证信息的AuthManager实例
        """
        if not self.users:
            logger.info("没有需要刷新的用户")
            return

        logger.info(f"开始刷新 {len(self.users)} 个用户的账户信息...")

        for uuid, user_data in list(self.users.items()):
            try:
                refresh_token = user_data.get("refresh_token")
                if not refresh_token:
                    logger.warning(f"用户 {uuid} 没有刷新令牌，跳过刷新")
                    continue

                # 使用AuthManager刷新用户验证信息
                logger.info(f"正在刷新用户: {user_data['username']} ({uuid})")
                new_data = await auth_manager.authenticate(
                    method=AuthMethod.MICROSOFT,
                    refresh_token=refresh_token
                )

                # 更新用户数据，保留原始UUID
                new_data["uuid"] = uuid
                await self.user_save(uuid, new_data)
                logger.info(f"用户 {new_data['username']} ({uuid}) 刷新成功")

            except Exception as e:
                logger.error(f"刷新用户 {uuid} 失败: {str(e)}")

        logger.info("所有用户刷新完成")
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)


    async def main():
        user_manager = UserManager("QCL/users.json")

        await user_manager.user_load()
        user_list = user_manager.list_all_users()
        username_list = [user['username'] for user in user_list]
        print(f"当前用户列表：[{', '.join(username_list)}]")

        """
                try:
            # 执行验证
            auth_result = await perform_authentication()
            # username = auth_result['username']
            # uuid = auth_result['uuid']
            # access_token = auth_result['access_token']
            # refresh_token = auth_result['refresh_token']
            # skins = auth_result['skins']
            # capes = auth_result['capes']

            # 打印验证结果
            print("\n验证成功!")
            print(f"用户名: {auth_result['username']}")
            print(f"UUID: {auth_result['uuid']}")
            # 为保证安全，令牌只输出前20位
            print(f"访问令牌: {auth_result['access_token'][:20]}...")
            print(f"刷新令牌: {auth_result['refresh_token'][:20]}...")


        except Exception as e:
            print(f"验证失败: {str(e)}")
        """


    asyncio.run(main())
