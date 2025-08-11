"""
Base resource class for Reya Trading API resources.
"""

from typing import Any, Optional

import logging

import httpx

from sdk._version import SDK_VERSION
from sdk.reya_rest_api.auth.signatures import SignatureGenerator
from sdk.reya_rest_api.config import TradingConfig


class BaseResource:
    """Base class for all Reya Trading API resources."""

    def __init__(self, config: TradingConfig, signature_generator: Optional[SignatureGenerator] = None):
        """
        Initialize the resource with configuration.

        Args:
            config: Trading API configuration
            signature_generator: Optional signature generator (will create one if not provided)
        """
        self.config = config
        self.api_url = config.api_url
        self.headers = {
            "X-SDK-Version": f"reya-python-sdk/{SDK_VERSION}",
            "User-Agent": f"reya-python-sdk/{SDK_VERSION}",
        }

        # Create signature generator if not provided
        if signature_generator is None and config.private_key:
            self.signature_generator: Optional[SignatureGenerator] = SignatureGenerator(config)
        else:
            self.signature_generator = signature_generator

        self.logger = logging.getLogger(f"reya_trading.{self.__class__.__name__}")

    def _get_endpoint_url(self, path: str) -> str:
        """
        Get the full URL for an API endpoint.

        Args:
            path: API endpoint path (without leading slash)

        Returns:
            Full URL for the API endpoint
        """
        # Ensure path doesn't start with a slash
        if path.startswith("/"):
            path = path[1:]

        # Ensure API URL doesn't end with a slash
        base_url = self.api_url
        if base_url.endswith("/"):
            base_url = base_url[:-1]

        return f"{base_url}/{path}"

    def _handle_response(self, response: httpx.Response, error_msg: str = "API request failed") -> Any:
        """
        Handle API response, raising exceptions for errors.

        Args:
            response: HTTP response from API
            error_msg: Error message prefix for exceptions

        Returns:
            Parsed JSON response

        """
        try:
            data: Any = response.json()
        except ValueError:
            self.logger.error(f"Failed to parse JSON response: {response.text}")
            raise ValueError(f"{error_msg}: Invalid JSON response")

        return data

    async def _get(self, endpoint: str, params: Optional[dict[str, Any]] = None) -> Any:
        """
        Make an async GET request to the API.

        Args:
            endpoint: API endpoint path
            params: Optional query parameters

        Returns:
            Parsed JSON response
        """
        url = self._get_endpoint_url(endpoint)
        self.logger.debug(f"GET {url} with params: {params}")

        async with httpx.AsyncClient(headers=self.headers) as client:
            response = await client.get(url, params=params)
            return self._handle_response(response, f"GET {endpoint} failed")

    async def _post(
        self,
        endpoint: str,
        data: dict[str, Any],
        headers: Optional[dict[str, str]] = None,
    ) -> Any:
        """
        Make an async POST request to the API.

        Args:
            endpoint: API endpoint path
            data: Request payload
            headers: Optional request headers

        Returns:
            Parsed JSON response
        """
        url = self._get_endpoint_url(endpoint)
        self.logger.debug(f"POST {url} with data: {data}")

        async with httpx.AsyncClient(headers=self.headers) as client:
            response = await client.post(url, json=data, headers=headers)
            return self._handle_response(response, f"POST {endpoint} failed")
