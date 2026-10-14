# growwapi SDK API Documentation

## Overview

`growwapi` is the foundational SDK for accessing Groww APIs and listening to live data streams. This package provides the core functionalities to interact with Groww's trading platform.

## Features

- Connect to Groww's API
- Place, modify, and cancel orders
- Retrieve order details, status, holdings, trade lists
- Subscribe and unsubscribe to market feeds
- Get live market data and updates

## Installation

Install the package using pip:

```bash
pip install growwapi
```

## Usage

### Authentication

To use the SDK, you need to authenticate using your API credentials. Set the following variables:

- `API_AUTH_TOKEN`: Your API authentication token.

### API Client

The `GrowwAPI` class provides methods to interact with the Groww API.

```python
from growwapi import GrowwAPI

groww_client = GrowwAPI("YOUR_API_KEY")

# Get the current orders. Will wait for 5 seconds or until the orders are received
orders = groww_client.get_order_list(timeout=5)
print(orders)
```

### Feed Client

The `GrowwFeed` class provides methods to subscribe to and receive Groww data streams and updates.

It can either be used synchronously to get the last updated data or asynchronously to trigger a callback whenever new data is received.

```python
from growwapi import GrowwFeed
from growwapi import GrowwAPI

groww_feed = GrowwFeed("YOUR_API_KEY")

# Synchronous Usage: Create a subscription and then get the LTP
groww_feed.subscribe_live_data(GrowwAPI.SEGMENT_CASH, "SWIGGY")
# Will wait for 3 seconds or until the LTP is received
ltp = groww_feed.get_stocks_ltp("SWIGGY", timeout=3)
print(ltp)


# Asynchronous Usage: Callback triggered whenever the LTP changes
def get_ltp_print():
  # As it was triggerred on data received, we can directly get the LTP
  ltp = groww_feed.get_stocks_ltp("RELIANCE")
  print(ltp)


groww_feed.subscribe_live_data(GrowwAPI.SEGMENT_CASH, "RELIANCE", on_data_received=get_ltp_print)
```

## Documentation

- [growwapi python client documentation](https://groww.in/trade-api/docs/python-sdk)
