"""
This module contains custom exception classes for the Groww SDK.
"""


class BaseGrowwException(Exception):
    """
    Base class for exceptions in the Groww SDK.

    Attributes:
        msg (str): The error message associated with the exception.
    """

    def __init__(self, msg: str) -> None:
        """
        Initializes the GrowwException with an error message.

        Args:
            msg (str): The error message.
        """
        super().__init__(msg)
        self.msg = msg


class GrowwAPIException(BaseGrowwException):
    """
    Base class for exceptions in the Groww SDK.

    Attributes:
        msg (str): The error message associated with the exception.
    """

    def __init__(self, msg: str, code: str) -> None:
        """
        Initializes the GrowwException with an error message.

        Args:
            msg (str): The error message.
        """
        super().__init__(msg)
        self.code = code

class GrowwAPIAuthenticationException(GrowwAPIException):
    """
    Exception raised when authentication to the Groww API fails.
    """

    def __init__(self) -> None:
        """
        Initializes the GrowwAPIAuthenticationException with an error message.
        """
        super().__init__("Authentication failed. Your API token has either expired or is invalid.", "401")

class GrowwAPIAuthorisationException(GrowwAPIException):
    """
    Exception raised when authorisation to the Groww API fails.
    """

    def __init__(self) -> None:
        """
        Initializes the GrowwAPIAuthenticationException with an error message.
        """
        super().__init__("Authorisation failed. Your API token does not have the required permissions.", "403")

class GrowwAPIBadRequestException(GrowwAPIException):
    """
    Exception raised when a bad request is made to the Groww API.
    """

    def __init__(self) -> None:
        """
        Initializes the GrowwAPIBadRequestException with an error message.
        """
        super().__init__("The request was invalid. Please check the request parameters.", "400")

class GrowwAPINotFoundException(GrowwAPIException):
    """
    Exception raised when a resource is not found in the Groww API.
    """

    def __init__(self) -> None:
        """
        Initializes the GrowwAPINotFoundException with an error message.
        """
        super().__init__("The requested resource was not found. Please check the URL or relevant parameters.", "404")

class GrowwAPIRateLimitException(GrowwAPIException):
    """
    Exception raised when the rate limit for the Groww API is exceeded.
    """

    def __init__(self) -> None:
        """
        Initializes the GrowwAPIRateLimitException with an error message.
        """
        super().__init__("The rate limit for the Groww API has been exceeded. Please try again later.", "429")

class GrowwAPITimeoutException(GrowwAPIException):
    """
    Exception raised when a request to the Groww API times out.
    """

    def __init__(self) -> None:
        """
        Initializes the GrowwAPITimeoutException with an error message.
        """
        super().__init__("The request to the Groww API timed out. Please try again.", "504")


class GrowwFeedException(BaseGrowwException):
    """
    Exception raised when a connection to the Groww feed fails.
    """


class GrowwFeedConnectionException(GrowwFeedException):
    """
    Exception raised when a connection to the Groww feed fails.
    """


class GrowwFeedNotSubscribedException(GrowwFeedException):
    """
    Exception raised when get is attempted on an un-subscribed feed.
    """

    def __init__(self, msg: str, topic: str) -> None:
        """
        Initializes with an error message and the topic that was not subscribed to.

        Args:
            msg (str): The error message.
            topic (str): The topic that must be subscribed to receive messages.
        """
        super().__init__(msg)
        self.topic = topic

class InstrumentNotFoundException(BaseGrowwException):
    """
    Exception raised when an instrument is not found in the Groww API.
    """

    def __init__(self) -> None:
        """
        Initializes the GrowwException with an error message.

        """
        super().__init__("The requested instrument was not found. Please check the symbol/token.")