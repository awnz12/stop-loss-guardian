// SPDX-License-Identifier: MIT
pragma solidity ^0.8.29;

/// @title MockPriceFeed — stand-in market data source on the origin chain.
/// @notice Emits `PriceUpdated` events that the reactive guardian subscribes to. In the demo
///         this replaces a real DEX/oracle feed: anyone can push a price to simulate market
///         movement (testnet only — obviously not a production oracle).
/// @dev Event shape is deliberate: `token` is indexed (arrives as topic1 of the LogRecord),
///      `price` and `updatedAt` are not (they arrive ABI-encoded in the LogRecord's data).
contract MockPriceFeed {
    event PriceUpdated(address indexed token, uint256 price, uint256 updatedAt);

    mapping(address => uint256) public priceOf;
    mapping(address => uint256) public updatedAtOf;

    /// @notice Push a new price for a token and emit the event the guardian watches.
    function pushPrice(address token, uint256 price) external {
        priceOf[token] = price;
        updatedAtOf[token] = block.timestamp;
        emit PriceUpdated(token, price, block.timestamp);
    }
}
