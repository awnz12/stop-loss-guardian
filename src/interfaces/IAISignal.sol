// SPDX-License-Identifier: MIT
pragma solidity ^0.8.29;

/// @title Interface for the on-chain AI stop-loss signal.
/// @notice The off-chain AI agent is part of the running system (Reactive Meets AI track):
///         it watches market conditions and arms / updates stop-loss parameters by writing
///         them to a contract implementing this interface. Every accepted update emits
///         `SignalUpdated`, which is the event the reactive guardian on Lasna subscribes to —
///         so an AI decision propagates cross-chain without any human or keeper in between.
/// @dev Two write paths are supported:
///      1. `updateSignal` — direct call, restricted to the AI operator address.
///      2. `updateSignalSigned` — anyone may relay an EIP-712 signature produced by the AI
///         operator off-chain (gasless for the AI; replay-protected by a per-token nonce
///         and a deadline).
interface IAISignal {
    /// @notice Current stop-loss parameters for one token.
    /// @param triggerPrice Price (same scale as the price feed) at or below which the stop fires.
    /// @param armed Whether the guardian should act on price events for this token.
    /// @param nonce Number of accepted updates so far; the next signed update must use nonce + 1.
    /// @param updatedAt Timestamp of the last accepted update.
    struct Signal {
        uint256 triggerPrice;
        bool armed;
        uint256 nonce;
        uint256 updatedAt;
    }

    /// @notice Emitted on every accepted update. The guardian subscribes to this exact event:
    ///         topic1 = token, data = abi.encode(triggerPrice, armed, nonce).
    event SignalUpdated(address indexed token, uint256 triggerPrice, bool armed, uint256 nonce);

    /// @notice Direct parameter update. Caller must be the AI operator.
    function updateSignal(address token, uint256 triggerPrice, bool armed) external;

    /// @notice Relayed parameter update carrying the AI operator's EIP-712 signature over
    ///         (token, triggerPrice, armed, nonce = current nonce + 1, deadline).
    function updateSignalSigned(
        address token,
        uint256 triggerPrice,
        bool armed,
        uint256 deadline,
        bytes calldata signature
    ) external;

    /// @notice Read the current parameters for a token.
    function getSignal(address token) external view returns (Signal memory);

    /// @notice The address allowed to write signals (directly, or by signature).
    function aiOperator() external view returns (address);
}
