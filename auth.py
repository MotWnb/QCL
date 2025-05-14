import asyncio
import base64
import hashlib
import json
import logging
import os
import webbrowser
from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, Optional, Tuple, List

import aiofiles
import aiohttp
import machineid
import msal
import pyperclip
from cryptography.fernet import Fernet

logger = logging.getLogger('QCL')

class AuthMethod(Enum):
    MICROSOFT = 1
    OFFLINE = 2
    THIRD_PARTY = 3

class IAuthenticator(ABC):
    @abstractmethod
    async def authenticate(self, refresh_token: Optional[str] = None) -> Dict:
        pass

class MicrosoftAuthenticator(IAuthenticator):
    def __init__(self, client_id: str = "de243363-2e6a-44dc-82cb-ea8d6b5cd98d",
                 redirect_uri: str = "http://localhost:8080/callback"):
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.scopes = ["XboxLive.signin"]
        self.xbox_auth_endpoint = "https://user.auth.xboxlive.com/user/authenticate"
        self.xsts_auth_endpoint = "https://xsts.auth.xboxlive.com/xsts/authorize"
        self.minecraft_auth_endpoint = "https://api.minecraftservices.com/authentication/login_with_xbox"
        self.minecraft_entitlements_endpoint = "https://api.minecraftservices.com/entitlements/mcstore"
        self.minecraft_profile_endpoint = "https://api.minecraftservices.com/minecraft/profile"
        self.msal_app = msal.PublicClientApplication(
            client_id=self.client_id,
            authority="https://login.microsoftonline.com/consumers"
        )

    async def authenticate(self, refresh_token: Optional[str] = None) -> Dict:
        try:
            microsoft_token = await self._get_microsoft_token(refresh_token)  # 使用MSAL获取Microsoft令牌
            xbl_token, xbl_uhs = await self._authenticate_with_xbox_live(microsoft_token["access_token"])
            xsts_token, xsts_uhs = await self._authenticate_with_xsts(xbl_token)
            minecraft_token = await self._authenticate_with_minecraft(xsts_uhs, xsts_token)
            if not await self._check_game_ownership(minecraft_token["access_token"]):
                raise Exception("该账号没有购买 Minecraft")
            profile = await self._get_minecraft_profile(minecraft_token["access_token"])
            result = {
                "username": profile["name"], "uuid": profile["id"],
                "access_token": minecraft_token["access_token"],
                "refresh_token": microsoft_token.get("refresh_token", ""),
                "skins": profile.get("skins", []), "capes": profile.get("capes", [])
            }
            logger.info(f"验证成功: {result['username']} ({result['uuid']})")
            return result
        except Exception as e:
            logger.error(f"验证失败: {str(e)}")
            raise

    async def _get_microsoft_token(self, refresh_token: Optional[str] = None) -> Dict:
        # 使用asyncio.to_thread在异步上下文中运行同步的MSAL方法
        if refresh_token:
            # 使用刷新令牌获取新的访问令牌
            result = await asyncio.to_thread(
                lambda: self.msal_app.acquire_token_by_refresh_token(
                    refresh_token=refresh_token,
                    scopes=self.scopes))
            if "error" in result:
                # 特别处理刷新令牌失效的情况
                if result.get("error") == "invalid_grant":
                    logger.warning("刷新令牌已过期，需要重新认证")
                    return await self._get_microsoft_token(None)  # 递归调用获取新令牌
                raise Exception(f"刷新令牌失败: {result.get('error_description')}")
            return result
        else:
            flow = await asyncio.to_thread(  # 使用设备代码流获取令牌
                lambda: self.msal_app.initiate_device_flow(scopes=self.scopes)
            )
            if "user_code" not in flow:
                raise Exception(f"创建设备流失败: {flow.get('error_description')}")
            print(f"请打开 {flow['verification_uri']}")
            print(f"并输入代码: {flow['user_code']}")
            print("代码已复制到剪贴板")
            pyperclip.copy(flow['user_code'])
            webbrowser.open(flow['verification_uri'])
            result = await asyncio.to_thread(  # 轮询等待用户授权
                lambda: self.msal_app.acquire_token_by_device_flow(flow)
            )
            if "error" in result:
                raise Exception(f"设备授权失败: {result.get('error_description')}")
            return result
    async def _authenticate_with_xbox_live(self, microsoft_token: str) -> Tuple[str, str]:
        url = self.xbox_auth_endpoint
        payload = {"Properties": {"AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com",
                                  "RpsTicket": f"d={microsoft_token}"}, "RelyingParty": "http://auth.xboxlive.com",
                   "TokenType": "JWT"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers={"Content-Type": "application/json", "Accept": "application/json"},
                                    json=payload) as response:
                response.raise_for_status()
                data = await response.json()
                return data["Token"], data["DisplayClaims"]["xui"][0]["uhs"]

    async def _authenticate_with_xsts(self, xbl_token: str) -> Tuple[str, str]:
        url = self.xsts_auth_endpoint
        payload = {"Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl_token]},
                   "RelyingParty": "rp://api.minecraftservices.com/", "TokenType": "JWT"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers={"Content-Type": "application/json", "Accept": "application/json"},
                                    json=payload) as response:
                if response.status == 401:
                    data = await response.json()
                    raise Exception(f"XSTS 验证失败: {data.get('XErr')} - {data.get('Message')}")
                response.raise_for_status()
                data = await response.json()
                return data["Token"], data["DisplayClaims"]["xui"][0]["uhs"]

    async def _authenticate_with_minecraft(self, uhs: str, xsts_token: str) -> Dict:
        url = self.minecraft_auth_endpoint
        payload = {"identityToken": f"XBL3.0 x={uhs};{xsts_token}"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers={"Content-Type": "application/json"}, json=payload) as response:
                response.raise_for_status()
                return await response.json()

    async def _check_game_ownership(self, access_token: str) -> bool:
        url = self.minecraft_entitlements_endpoint
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers={"Authorization": f"Bearer {access_token}"}) as response:
                response.raise_for_status()
                data = await response.json()
                items = data.get("items", [])
                return any(item.get("name") == "game_minecraft" for item in items)

    async def _get_minecraft_profile(self, access_token: str) -> Dict:
        url = self.minecraft_profile_endpoint
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers={"Authorization": f"Bearer {access_token}"}) as response:
                response.raise_for_status()
                return await response.json()

class OfflineAuthenticator(IAuthenticator):
    async def authenticate(self, refresh_token: Optional[str] = None) -> Dict:
        print("使用离线验证...")
        return {"username": "Player", "uuid": "00000000-0000-0000-0000-000000000000", "access_token": "offline_token",
                "refresh_token": "", "skins": [], "capes": []}


class ThirdPartyAuthenticator(IAuthenticator):
    async def authenticate(self, refresh_token: Optional[str] = None) -> Dict:
        print("使用第三方验证...")
        return {"username": "ThirdPartyPlayer", "uuid": "11111111-1111-1111-1111-111111111111",
                "access_token": "third_party_token", "refresh_token": "", "skins": [], "capes": []}


class AuthManager:
    def __init__(self):
        self._authenticators = {
            AuthMethod.MICROSOFT: MicrosoftAuthenticator(),
            AuthMethod.OFFLINE: OfflineAuthenticator(),
            AuthMethod.THIRD_PARTY: ThirdPartyAuthenticator()
        }

    async def authenticate(self, method: AuthMethod, refresh_token: Optional[str] = None) -> Dict:
        if method not in self._authenticators:
            raise ValueError(f"不支持的验证方式: {method}")
        return await self._authenticators[method].authenticate(refresh_token)

    @staticmethod
    async def prompt_for_auth_method() -> AuthMethod:
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

class UserManager:
    def __init__(self, data_file: str = "users.json"):
        self.data_file = data_file
        self.users = {}

    @staticmethod
    def _get_fernet_key() -> bytes:
        device_id = machineid.id().encode('utf-8')
        sha256_hash = hashlib.sha256(device_id).digest()
        return base64.urlsafe_b64encode(sha256_hash)

    @staticmethod
    def encrypt_dict(data: dict) -> str:
        key = UserManager._get_fernet_key()
        cipher = Fernet(key)
        return cipher.encrypt(json.dumps(data).encode('utf-8')).decode('ascii')

    @staticmethod
    def decrypt_dict(encrypted_data: str) -> dict:
        key = UserManager._get_fernet_key()
        cipher = Fernet(key)
        return json.loads(cipher.decrypt(encrypted_data.encode('ascii')).decode('utf-8'))

    async def user_load(self) -> Dict[str, Dict]:
        try:
            if os.path.exists(self.data_file):
                async with aiofiles.open(self.data_file, 'r', encoding='utf-8') as file:
                    content = await file.read()
                    self.users = self.decrypt_dict(content)
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
        if not uuid:
            raise ValueError("UUID不能为空")
        required_fields = ["username", "access_token", "refresh_token"]
        for field in required_fields:
            if field not in user_data:
                raise ValueError(f"用户数据缺少必要字段: {field}")
        self.users[uuid] = user_data
        await self._save_to_file()
        logger.info(f"用户数据已保存: {user_data['username']} ({uuid})")

    async def _save_to_file(self) -> None:
        try:
            async with aiofiles.open(self.data_file, 'w', encoding='utf-8') as file:
                await file.write(self.encrypt_dict(self.users))
        except Exception as e:
            logger.error(f"保存用户数据失败: {str(e)}")

    def user_get(self, uuid: str) -> Optional[Dict]:
        return self.users.get(uuid)

    async def user_delete(self, uuid: str) -> bool:
        if uuid in self.users:
            del self.users[uuid]
            await self._save_to_file()
            logger.info(f"用户数据已删除: {uuid}")
            return True
        return False

    def list_all_users(self) -> List[Dict]:
        return list(self.users.values())

    async def refresh_all_users(self, auth_manager) -> None:
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
                logger.info(f"正在刷新用户: {user_data['username']} ({uuid})")
                new_data = await auth_manager.authenticate(method=AuthMethod.MICROSOFT, refresh_token=refresh_token)
                new_data["uuid"] = uuid
                await self.user_save(uuid, new_data)
                logger.info(f"用户 {new_data['username']} ({uuid}) 刷新成功")
            except Exception as e:
                logger.error(f"刷新用户 {uuid} 失败: {str(e)}")
        logger.info("所有用户刷新完成")

async def perform_authentication(refresh_token: Optional[str] = None) -> Dict:
    manager = AuthManager()
    auth_method = await manager.prompt_for_auth_method()
    auth_result = await manager.authenticate(auth_method, refresh_token)
    user_manager = UserManager("QCL/users.ini")
    await user_manager.user_save(auth_result["uuid"], auth_result)
    return auth_result

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    async def main():
        user_manager = UserManager("QCL/users.ini")
        await user_manager.user_load()
        user_list = user_manager.list_all_users()
        username_list = [user['username'] for user in user_list]
        print(f"当前用户列表：[{', '.join(username_list)}]")
        await user_manager.refresh_all_users(AuthManager())
        try:
            auth_result = await perform_authentication()
            print("\n验证成功!")
            print(f"用户名: {auth_result['username']}")
            print(f"UUID: {auth_result['uuid']}")
            print(f"访问令牌: {auth_result['access_token'][:20]}...")
            print(f"刷新令牌: {auth_result['refresh_token'][:20]}...")
        except Exception as e:
            print(f"验证失败: {str(e)}")

    asyncio.run(main())