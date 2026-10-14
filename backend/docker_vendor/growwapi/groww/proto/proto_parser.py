from typing import Type, Optional

from google.protobuf.json_format import MessageToDict

from growwapi.groww.constants import FeedConstants
from growwapi.groww.proto.stocks_socket_response_pb2 import (
    StocksSocketResponseProtoDto,
    StocksMarketInfoProto,
)
from growwapi.groww.proto.position_socket_pb2 import (
    PositionDetailProto,
)
from growwapi.groww.proto.stock_orders_socket_response_pb2 import (
    OrderDetailsBroadCastDto,
)


def _parse_data_to_proto_model(
    data: any,
    proto: Type[any],
) -> any:
  """
  Parse the proto object to the model object.

  Args:
      data (any): The data to parse.
      proto (Type): The proto class.
  """
  proto_object = proto()
  proto_object.ParseFromString(data)
  return proto_object


def _transform_proto_data_to_dict(proto_data: any) -> dict[str, any]:
  """
  Parse the proto object to the model object.

  Args:
      proto_data (any): The data to parse.
      model (Type[BaseGrowwModel]): The model class.

  Returns:
      dict[str, any]: The model object.
  """
  return MessageToDict(proto_data, preserving_proto_field_name=True, always_print_fields_with_no_presence=True)

parse_map = {
        FeedConstants.LIVE_DATA: lambda d: _transform_proto_data_to_dict(
            _parse_data_to_proto_model(d, StocksSocketResponseProtoDto).stockLivePrice),
        FeedConstants.MARKET_DEPTH: lambda d: _transform_proto_data_to_dict(
            _parse_data_to_proto_model(d, StocksSocketResponseProtoDto).stocksMarketDepth),
        FeedConstants.ORDER_UPDATE: lambda d: _transform_proto_data_to_dict(
            _parse_data_to_proto_model(d, OrderDetailsBroadCastDto).orderDetailUpdateDto),
        FeedConstants.POSITION_UPDATE: lambda d: (
            lambda p: {
                "symbolIsin": p.positionInfo.symbolIsin,
                "exchangePosition": {
                    "BSE": _transform_proto_data_to_dict(p.positionInfo.BSE),
                    "NSE": _transform_proto_data_to_dict(p.positionInfo.NSE),
                }
            })(_parse_data_to_proto_model(d, PositionDetailProto)),
        FeedConstants.LIVE_INDEX: lambda d: _transform_proto_data_to_dict(
            _parse_data_to_proto_model(d, StocksSocketResponseProtoDto).stocksLiveIndices),
    }


def get_data_dict(data: any,
    feed_type: FeedConstants) -> Optional[dict[str, any]]:
  """Get the parsed data dictionary for a given feed type."""

  if feed_type not in parse_map:
    raise ValueError(f"Unsupported feed type: {feed_type}")

  if data is None:
    return None

  parsed_data = parse_map[feed_type](data)

  if feed_type == FeedConstants.ORDER_UPDATE:
    if "buySell" in parsed_data:
      buy_sell = parsed_data["buySell"]
      parsed_data.pop("buySell")
      if buy_sell == "B":
        parsed_data["transactionType"] = "BUY"
      elif buy_sell == "S":
        parsed_data["transactionType"] = "SELL"
    
    if "guiOrderId" in parsed_data:
      parsed_data.pop("guiOrderId")
    
    if "orderType" in parsed_data:
      order_type = parsed_data["orderType"]
      if order_type == "MKT":
        parsed_data["orderType"] = "MARKET"
      elif order_type == "L":
        parsed_data["orderType"] = "LIMIT"
  

    
  return parsed_data  
