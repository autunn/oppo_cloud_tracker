"""OPPO Cloud Selenium API Client."""

from __future__ import annotations

import asyncio
import contextlib

from selenium import webdriver
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.remote.client_config import ClientConfig

from custom_components.oppo_cloud_tracker.const import (
    CONF_OPPO_CLOUD_FIND_URL,
    CONF_OPPO_CLOUD_LOGIN_URL,
    LOGGER,
)
from custom_components.oppo_cloud_tracker.data import OppoCloudDevice

from .gcj2wgs import gcj2wgs


class OppoCloudApiClientError(Exception):
    """Exception to indicate a general API error."""

    def __init__(self, message: str = "unexpected") -> None:
        """Initialize the OppoCloudApiClientError with a message."""
        super().__init__(message)


class OppoCloudApiClientCommunicationError(OppoCloudApiClientError):
    """Exception to indicate a communication error."""

    def __init__(self, context: str = "unexpected") -> None:
        """Initialize the OppoCloudApiClientCommunicationError with a message."""
        super().__init__(f"when {context}")


class OppoCloudApiClientAuthenticationError(OppoCloudApiClientError):
    """Exception to indicate an authentication error."""

    def __init__(self, context: str = "unexpected") -> None:
        """Initialize the OppoCloudApiClientAuthenticationError with a message."""
        super().__init__(f"when {context}")


class OppoCloudApiClient:
    """OPPO Cloud (HeyTap) API Client using Selenium."""

    def __init__(
        self,
        username: str,
        password: str,
        remote_browser_url: str,
    ) -> None:
        """Initialize OPPO Cloud API Client."""
        self._username = username
        self._password = password
        self._remote_browser_url = remote_browser_url
        self._driver: webdriver.Remote | None = None
        self._keep_session = False

    def set_keep_browser_session(self, *, keep_session: bool) -> None:
        """Set whether to keep the browser session (synchronous version)."""
        self._keep_session = keep_session

    async def async_set_keep_browser_session(self, *, keep_session: bool) -> None:
        """Set whether to keep the browser session between updates."""
        self._keep_session = keep_session
        if not keep_session and self._driver is not None:
            await self.async_cleanup()

    def _get_or_create_driver(self) -> webdriver.Remote:
        """Get existing WebDriver instance or create a new one."""
        if self._driver is not None:
            try:
                _ = self._driver.current_url
            except WebDriverException:
                self._driver = None
            else:
                return self._driver

        url = self._remote_browser_url.strip()
        try:
            chrome_options = ChromeOptions()
            chrome_options.add_argument("--headless")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--window-size=1920,1080")

            LOGGER.info("Connecting to Selenium Grid at %s", url)

            client_config = ClientConfig(remote_server_addr=url, timeout=30)
            self._driver = webdriver.Remote(
                command_executor=url,
                options=chrome_options,
                client_config=client_config,
            )
        except OppoCloudApiClientError:
            raise
        except Exception as exception:
            self._driver = None
            msg = f"connecting to remote browser at {url} - {exception}"
            raise OppoCloudApiClientCommunicationError(msg) from exception

        return self._driver

    def _cleanup_driver(self) -> None:
        """Clean up the WebDriver instance (sync)."""
        if not self._driver:
            return
        try:
            self._driver.quit()
        except WebDriverException:
            pass
        finally:
            self._driver = None

    async def async_cleanup(self) -> None:
        """Clean up WebDriver resources."""
        if not self._driver:
            return
        await asyncio.get_running_loop().run_in_executor(None, self._cleanup_driver)

    async def async_login_oppo_cloud(self) -> None:
        """Log in to OPPO Cloud using Selenium."""
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, self._login_oppo_cloud
            )
        except OppoCloudApiClientAuthenticationError:
            raise
        except OppoCloudApiClientCommunicationError:
            raise
        except TimeoutException as exception:
            msg = f"login - {exception}"
            raise OppoCloudApiClientError(msg) from exception
        except Exception as exception:
            msg = f"Unexpected login - {exception}"
            raise OppoCloudApiClientError(msg) from exception

    def _login_oppo_cloud(self) -> None:
        """Log in to OPPO Cloud using Selenium (sync)."""
        driver = self._get_or_create_driver()
        wait = WebDriverWait(driver, 10) 

        driver.get(CONF_OPPO_CLOUD_LOGIN_URL)
        LOGGER.info("Navigated to OPPO Cloud login page")

        wait.until(
            expected_conditions.element_to_be_clickable(
                (
                    By.XPATH,
                    "//header//*[normalize-space()='Sign in'] | "
                    "//*[@role='banner']//*[normalize-space()='Sign in']",
                )
            )
        ).click()

        login_iframe = wait.until(
            expected_conditions.presence_of_element_located((By.CSS_SELECTOR, "iframe"))
        )
        driver.switch_to.frame(login_iframe)

        try:
            username_el = wait.until(
                expected_conditions.visibility_of_element_located(
                    (By.CSS_SELECTOR, "input[type='tel']")
                )
            )
            username_el.send_keys(Keys.CONTROL + "a")
            username_el.send_keys(Keys.DELETE)
            username_el.send_keys(self._username)

            password_el = wait.until(
                expected_conditions.visibility_of_element_located(
                    (By.CSS_SELECTOR, "input[type='password']")
                )
            )
            password_el.send_keys(Keys.CONTROL + "a")
            password_el.send_keys(Keys.DELETE)
            password_el.send_keys(self._password)

            observer_script = """
window.__capturedErrors = [];
const regex = /incorrect|error|fail|wrong|invalid/i;
const observer = new MutationObserver(mutations => {
    for (const m of mutations) {
        for (const node of m.addedNodes) {
            const text = (node.textContent || '').trim();
            if (text && text.length < 500 && regex.test(text)) {
                window.__capturedErrors.push(text.substring(0, 200));
            }
        }
        if (m.type === 'characterData') {
            const text = (m.target.textContent || '').trim();
            if (text && text.length < 500 && regex.test(text)) {
                window.__capturedErrors.push(text.substring(0, 200));
            }
        }
    }
});
observer.observe(document, { childList: true, subtree: true, characterData: true });
            """
            driver.execute_script(observer_script)

            sign_in_btn = wait.until(
                lambda d: next(
                    (
                        el
                        for el in d.find_elements(By.CSS_SELECTOR, "[role='button']")
                        if el.is_displayed()
                        and "Sign in" in (el.text or "")
                        and "uc-button-disabled"
                        not in (el.get_attribute("class") or "")
                    ),
                    None,
                )
            )
            sign_in_btn.click()

            with contextlib.suppress(TimeoutException):
                agree_btn = WebDriverWait(driver, 5).until(
                    lambda d: next(
                        (
                            el
                            for el in d.find_elements(
                                By.CSS_SELECTOR, "[role='button']"
                            )
                            if el.is_displayed()
                            and "Agree and continue" in (el.text or "")
                        ),
                        None,
                    )
                )
                agree_btn.click()
                LOGGER.info("Agreed to ToS")

            try:
                wait.until(
                    lambda d: not d.current_url.startswith(CONF_OPPO_CLOUD_LOGIN_URL)
                )
                LOGGER.info("OPPO Cloud login successful")
            except TimeoutException as exception:
                captured = driver.execute_script("return window.__capturedErrors || []")
                clean_captured = []
                for s in captured:
                    normalized = " ".join(s.split())
                    if normalized:
                        clean_captured.append(normalized)
                captured_str = ", ".join(dict.fromkeys(clean_captured))
                msg = f"login, looks like {captured_str}" if captured else "login"
                raise OppoCloudApiClientAuthenticationError(msg) from exception
        finally:
            with contextlib.suppress(WebDriverException):
                driver.switch_to.default_content()

    async def async_get_data(self) -> list[OppoCloudDevice]:
        """Get device location data from OPPO Cloud."""
        try:
            if not self._keep_session:
                await self.async_login_oppo_cloud()
            result = await asyncio.get_running_loop().run_in_executor(
                None, self._get_devices_data
            )
        except OppoCloudApiClientAuthenticationError:
            LOGGER.info("OPPO Cloud not logged in, attempting to log in")
            await self.async_login_oppo_cloud()
            return await self.async_get_data()
        except TimeoutException as exception:
            msg = f"get_devices_data - {exception}"
            raise OppoCloudApiClientError(msg) from exception
        except Exception as exception:
            msg = f"Unexpected get_devices_data - {exception}"
            raise OppoCloudApiClientError(msg) from exception
        finally:
            if not self._keep_session:
                await self.async_cleanup()
        return result

    def _get_devices_data(self) -> list[OppoCloudDevice]:
        """Get device locations using Selenium WebDriver."""
        driver = self._get_or_create_driver()
        driver.get(CONF_OPPO_CLOUD_FIND_URL)
        wait = WebDriverWait(driver, 10)

        if not driver.current_url.startswith(CONF_OPPO_CLOUD_FIND_URL):
            msg = "not logged in or page redirected unexpectedly"
            raise OppoCloudApiClientAuthenticationError(msg)

        try:
            wait.until(
                lambda d: d.find_element(
                    By.CSS_SELECTOR, "div.device_location"
                ).value_of_css_property("display")
                == "none"
            )
        except TimeoutException:
            LOGGER.warning("device_location overlay did not hide")

        # 模拟点击列表以在网页渲染电量元素
        try:
            device_items = wait.until(
                expected_conditions.presence_of_all_elements_located(
                    (By.CSS_SELECTOR, "#device-list .device-list ul > li")
                )
            )
            for item in device_items:
                driver.execute_script("arguments[0].click();", item)
                import time
                time.sleep(1.5) # 给时间让 HTML 渲染
        except Exception as exception:
            LOGGER.warning("Failed to click device item for details: %s", exception)

        # 注入 JavaScript 从 DOM 抓取电量
        device_data = driver.execute_script(
            """
            if (!window.$findVm || !window.$findVm.deviceList) return null;
            var devices = JSON.parse(JSON.stringify(window.$findVm.deviceList));
            
            var globalBattery = null;
            var batteryEl = document.querySelector('.info-battery .count');
            if (batteryEl) {
                globalBattery = (batteryEl.innerText || batteryEl.textContent).replace('%', '').trim();
            }
            
            for (var i = 0; i < devices.length; i++) {
                var localBatteryEl = null;
                var liElems = document.querySelectorAll("#device-list .device-list ul > li");
                if (liElems && liElems.length > i) {
                    localBatteryEl = liElems[i].querySelector('.info-battery .count');
                }
                if (localBatteryEl) {
                    devices[i]._domBattery = (localBatteryEl.innerText || localBatteryEl.textContent).replace('%', '').trim();
                } else if (globalBattery) {
                    devices[i]._domBattery = globalBattery;
                }
            }
            return {
                deviceList: devices,
                points: window.$findVm.points || []
            };
            """
        )

        if not device_data:
            LOGGER.warning("$findVm data is unexpected")
            return []

        devices = self._parse_device_data(
            device_data["deviceList"], device_data.get("points", [])
        )

        return devices

    def _parse_device_data(
        self, devices: list[dict], points: list[dict]
    ) -> list[OppoCloudDevice]:
        """Parse a single device data."""
        result: list[OppoCloudDevice] = []

        for idx, device in enumerate(devices):
            device_model = device.get("deviceName", "Unknown Device")
            is_online = (
                device.get("onlineStatus") == 1
                or device.get("locationStatus") == "online"
            )

            poi = device.get("poi", "") or device.get("simplePoi", "")
            if "·" in poi:
                location_name, last_seen = [s.strip() for s in poi.split(" · ", 1)]
            else:
                location_name = poi.strip()
                last_seen = device.get("poiTime")

            latitude, longitude = None, None
            if idx < len(points) and points[idx]:
                try:
                    latitude, longitude = gcj2wgs(points[idx]["lat"], points[idx]["lng"])
                except Exception:
                    pass

            # 提取抓取到的电量
            battery_level_raw = device.get("_domBattery") or device.get("batteryLevel") or device.get("batteryPercent")
            
            battery_level = None
            if battery_level_raw is not None:
                try:
                    battery_level = int(str(battery_level_raw).replace("%", "").strip())
                except (ValueError, TypeError):
                    pass

            result.append(
                OppoCloudDevice(
                    device_model=device_model,
                    location_name=location_name,
                    latitude=latitude,
                    longitude=longitude,
                    last_seen=last_seen,
                    is_online=is_online,
                    battery_level=battery_level, 
                )
            )

        return result

    async def async_test_connection(self) -> bool:
        """Test connection to Selenium Grid and basic functionality."""
        try:
            return await asyncio.get_running_loop().run_in_executor(
                None, self._test_connection
            )
        except Exception as exception:
            msg = f"Connection test failed - {exception}"
            raise OppoCloudApiClientCommunicationError(msg) from exception

    def _test_connection(self) -> bool:
        """Test Selenium Grid connection (sync)."""
        try:
            driver = self._get_or_create_driver()
            driver.get(CONF_OPPO_CLOUD_LOGIN_URL)
            body = WebDriverWait(driver, 10).until(
                expected_conditions.presence_of_element_located((By.TAG_NAME, "body"))
            )
        except Exception:
            self._cleanup_driver()
            raise
        return True

async def _debug_main() -> None:
    pass

if __name__ == "__main__":
    asyncio.run(_debug_main())
