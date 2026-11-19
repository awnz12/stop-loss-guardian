// SPDX-License-Identifier: MIT
pragma solidity ^0.8.29;

import { IPayable } from "@reactive/src/interfaces/IPayable.sol";
import { AbstractCallback } from "@reactive/src/base/AbstractCallback.sol";
import { MockPositionVault } from "./MockPositionVault.sol";

/// @title StopLossExecutor — destination-chain end of the stop-loss loop.
/// @notice Deployed on the destination chain (Base Sepolia for the demo). Receives the
///         guardian's callback through the chain's callback proxy and closes every
///         protected position in the triggered token.
/// @dev Authentication follows the docs exactly, both guards always:
///        - `onlyServiceProvider`: the caller must be the callback proxy (Base Sepolia:
///          0x476F44b11F5C942c58f4c8E3d0300E5710743585), which also bills this contract for
///          the callback in the chain's native token — keep it funded.
///        - `onlyCallbackSender(reactive_)`: the first payload argument, overwritten by
///          the network with the requesting reactive contract's address, must be our
///          guardian (immutable — a wrong address at deployment means redeploy).
contract StopLossExecutor is AbstractCallback {
    /// @notice The vault holding the protected mock positions.
    MockPositionVault public immutable vault;

    event StopLossExecuted(address indexed token, uint256 triggerPrice, uint256 positionsExited);

    /// @param callbackProxy_ Callback proxy on this chain (IPayable so the proxy can bill us).
    /// @param guardian_ StopLossGuardian address on Reactive Network (Lasna).
    /// @param vault_ MockPositionVault on this chain.
    constructor(IPayable callbackProxy_, address guardian_, MockPositionVault vault_)
        payable
        AbstractCallback(callbackProxy_, guardian_)
    {
        vault = vault_;
    }

    /// @notice Callback entry point. Signature must match the payload the guardian builds:
    ///         exitPosition(address,address,uint256) with the first argument reserved.
    /// @param reactive_ Injected by the network: the guardian's address (placeholder in payload).
    /// @param token Token whose price hit the stop.
    /// @param triggerPrice The price print that fired the stop.
    function exitPosition(address reactive_, address token, uint256 triggerPrice)
        external
        onlyServiceProvider
        onlyCallbackSender(reactive_)
    {
        uint256 exited = vault.exitAllForToken(token, triggerPrice);
        emit StopLossExecuted(token, triggerPrice, exited);
    }

    /// @notice Settle outstanding debt to the callback proxy from this contract's balance.
    function coverDebt() external {
        _coverDebt();
    }
}
