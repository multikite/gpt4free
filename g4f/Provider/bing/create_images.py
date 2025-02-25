"""
This module provides functionalities for creating and managing images using Bing's service.
It includes functions for user login, session creation, image creation, and processing.
"""
from __future__ import annotations

import asyncio
import time
import json
import os
from aiohttp import ClientSession, BaseConnector
from urllib.parse import quote
from typing import Generator, List, Dict

try:
    from bs4 import BeautifulSoup
    has_requirements = True
except ImportError:
    has_requirements = False

from ..create_images import CreateImagesProvider
from ..helper import get_cookies, get_connector
from ...webdriver import WebDriver, get_driver_cookies, get_browser
from ...base_provider import ProviderType
from ...image import ImageResponse
from ...errors import MissingRequirementsError, MissingAccessToken

BING_URL = "https://www.bing.com"
TIMEOUT_LOGIN = 1200
TIMEOUT_IMAGE_CREATION = 300
ERRORS = [
    "this prompt is being reviewed",
    "this prompt has been blocked",
    "we're working hard to offer image creator in more languages",
    "we can't create your images right now"
]
BAD_IMAGES = [
    "https://r.bing.com/rp/in-2zU3AJUdkgFe7ZKv19yPBHVs.png",
    "https://r.bing.com/rp/TX9QuO3WzcCJz1uaaSwQAz39Kb0.jpg",
]

def wait_for_login(driver: WebDriver, timeout: int = TIMEOUT_LOGIN) -> None:
    """
    Waits for the user to log in within a given timeout period.

    Args:
        driver (WebDriver): Webdriver for browser automation.
        timeout (int): Maximum waiting time in seconds.

    Raises:
        RuntimeError: If the login process exceeds the timeout.
    """
    driver.get(f"{BING_URL}/")
    start_time = time.time()
    while not driver.get_cookie("_U"):
        if time.time() - start_time > timeout:
            raise RuntimeError("Timeout error")
        time.sleep(0.5)

def create_session(cookies: Dict[str, str], proxy: str = None, connector: BaseConnector = None) -> ClientSession:
    """
    Creates a new client session with specified cookies and headers.

    Args:
        cookies (Dict[str, str]): Cookies to be used for the session.

    Returns:
        ClientSession: The created client session.
    """
    headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "accept-encoding": "gzip, deflate, br",
        "accept-language": "en-US,en;q=0.9,zh-CN;q=0.8,zh-TW;q=0.7,zh;q=0.6",
        "content-type": "application/x-www-form-urlencoded",
        "referrer-policy": "origin-when-cross-origin",
        "referrer": "https://www.bing.com/images/create/",
        "origin": "https://www.bing.com",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/111.0.0.0 Safari/537.36 Edg/111.0.1661.54",
        "sec-ch-ua": "\"Microsoft Edge\";v=\"111\", \"Not(A:Brand\";v=\"8\", \"Chromium\";v=\"111\"",
        "sec-ch-ua-mobile": "?0",
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "same-origin",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
    }
    if cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    return ClientSession(headers=headers, connector=get_connector(connector, proxy))

async def create_images(session: ClientSession, prompt: str, proxy: str = None, timeout: int = TIMEOUT_IMAGE_CREATION) -> List[str]:
    """
    Creates images based on a given prompt using Bing's service.

    Args:
        session (ClientSession): Active client session.
        prompt (str): Prompt to generate images.
        proxy (str, optional): Proxy configuration.
        timeout (int): Timeout for the request.

    Returns:
        List[str]: A list of URLs to the created images.

    Raises:
        RuntimeError: If image creation fails or times out.
    """
    if not has_requirements:
        raise MissingRequirementsError('Install "beautifulsoup4" package')
    url_encoded_prompt = quote(prompt)
    payload = f"q={url_encoded_prompt}&rt=4&FORM=GENCRE"
    url = f"{BING_URL}/images/create?q={url_encoded_prompt}&rt=4&FORM=GENCRE"
    async with session.post(url, allow_redirects=False, data=payload, timeout=timeout) as response:
        response.raise_for_status()
        text = (await response.text()).lower()
        for error in ERRORS:
            if error in text:
                raise RuntimeError(f"Create images failed: {error}")
    if response.status != 302:
        url = f"{BING_URL}/images/create?q={url_encoded_prompt}&rt=3&FORM=GENCRE"
        async with session.post(url, allow_redirects=False, proxy=proxy, timeout=timeout) as response:
            if response.status != 302:
                raise RuntimeError(f"Create images failed. Code: {response.status}")

    redirect_url = response.headers["Location"].replace("&nfy=1", "")
    redirect_url = f"{BING_URL}{redirect_url}"
    request_id = redirect_url.split("id=")[1]
    async with session.get(redirect_url) as response:
        response.raise_for_status()

    polling_url = f"{BING_URL}/images/create/async/results/{request_id}?q={url_encoded_prompt}"
    start_time = time.time()
    while True:
        if time.time() - start_time > timeout:
            raise RuntimeError(f"Timeout error after {timeout} sec")
        async with session.get(polling_url) as response:
            if response.status != 200:
                raise RuntimeError(f"Polling images faild. Code: {response.status}")
            text = await response.text()
            if not text:
                await asyncio.sleep(1)
            else:
                break
    error = None
    try:
        error = json.loads(text).get("errorMessage")
    except:
        pass
    if error == "Pending":
        raise RuntimeError("Prompt is been blocked")
    elif error:
        raise RuntimeError(error)
    return read_images(text)

def read_images(html_content: str) -> List[str]:
    """
    Extracts image URLs from the HTML content.

    Args:
        html_content (str): HTML content containing image URLs.

    Returns:
        List[str]: A list of image URLs.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    tags = soup.find_all("img", class_="mimg")
    images = [img["src"].split("?w=")[0] for img in tags]
    if any(im in BAD_IMAGES for im in images):
        raise RuntimeError("Bad images found")
    if not images:
        raise RuntimeError("No images found")
    return images

def get_cookies_from_browser(proxy: str = None) -> dict[str, str]:
    """
    Retrieves cookies from the browser using webdriver.

    Args:
        proxy (str, optional): Proxy configuration.

    Returns:
        dict[str, str]: Retrieved cookies.
    """
    with get_browser(proxy=proxy) as driver:
        wait_for_login(driver)
        time.sleep(1)
        return get_driver_cookies(driver)

class CreateImagesBing:
    """A class for creating images using Bing."""
    
    def __init__(self, cookies: dict[str, str] = {}, proxy: str = None) -> None:
        self.cookies = cookies
        self.proxy = proxy
    
    def create_completion(self, prompt: str) -> Generator[ImageResponse, None, None]:
        """
        Generator for creating imagecompletion based on a prompt.

        Args:
            prompt (str): Prompt to generate images.

        Yields:
            Generator[str, None, None]: The final output as markdown formatted string with images.
        """
        cookies = self.cookies or get_cookies(".bing.com", False)
        if "_U" not in cookies:
            login_url = os.environ.get("G4F_LOGIN_URL")
            if login_url:
                yield f"Please login: [Bing]({login_url})\n\n"
            try:
                self.cookies = get_cookies_from_browser(self.proxy)
            except MissingRequirementsError as e:
                raise MissingAccessToken(f'Missing "_U" cookie. {e}')
        yield asyncio.run(self.create_async(prompt))

    async def create_async(self, prompt: str) -> ImageResponse:
        """
        Asynchronously creates a markdown formatted string with images based on the prompt.

        Args:
            prompt (str): Prompt to generate images.

        Returns:
            str: Markdown formatted string with images.
        """
        cookies = self.cookies or get_cookies(".bing.com", False)
        if "_U" not in cookies:
            raise MissingAccessToken('Missing "_U" cookie')
        proxy = os.environ.get("G4F_PROXY")
        async with create_session(cookies, proxy) as session:
            images = await create_images(session, prompt, self.proxy)
            return ImageResponse(images, prompt)
    
service = CreateImagesBing()

def patch_provider(provider: ProviderType) -> CreateImagesProvider:
    """
    Patches a provider to include image creation capabilities.

    Args:
        provider (ProviderType): The provider to be patched.

    Returns:
        CreateImagesProvider: The patched provider with image creation capabilities.
    """
    return CreateImagesProvider(
        provider,
        service.create_completion,
        service.create_async
    )