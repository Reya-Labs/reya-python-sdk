"""
Base resource class for Reya Trading API resources.
"""
import logging
from typing import Dict, Any, Optional, Union
import requests

from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_rest_api.auth.signatures import SignatureGenerator


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
        
        # Create signature generator if not provided
        if signature_generator is None and config.private_key:
            self.signature_generator = SignatureGenerator(config)
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
        if path.startswith('/'):
            path = path[1:]
            
        # Ensure API URL doesn't end with a slash
        base_url = self.api_url
        if base_url.endswith('/'):
            base_url = base_url[:-1]
            
        return f"{base_url}/{path}"
    
    def _handle_response(
        self, 
        response: requests.Response,
        error_msg: str = "API request failed"
    ) -> Dict[str, Any]:
        """
        Handle API response, raising exceptions for errors.
        
        Args:
            response: HTTP response from API
            error_msg: Error message prefix for exceptions
            
        Returns:
            Parsed JSON response

        """
        try:
            data = response.json()
        except ValueError:
            self.logger.error(f"Failed to parse JSON response: {response.text}")
            raise ValueError(f"{error_msg}: Invalid JSON response")
            
        return data
        
    def _get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Make a GET request to the API.
        
        Args:
            endpoint: API endpoint path
            params: Optional query parameters
            
        Returns:
            Parsed JSON response
        """
        url = self._get_endpoint_url(endpoint)
        self.logger.debug(f"GET {url} with params: {params}")
        
        response = requests.get(url, params=params)
        return self._handle_response(response, f"GET {endpoint} failed")
        
    def _post(
        self, 
        endpoint: str, 
        data: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Make a POST request to the API.
        
        Args:
            endpoint: API endpoint path
            data: Request payload
            headers: Optional request headers
            
        Returns:
            Parsed JSON response
        """
        url = self._get_endpoint_url(endpoint)
        self.logger.debug(f"POST {url} with data: {data}")
        
        response = requests.post(url, json=data, headers=headers)
        return self._handle_response(response, f"POST {endpoint} failed")
