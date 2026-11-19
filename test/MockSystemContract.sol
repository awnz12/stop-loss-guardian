// SPDX-License-Identifier: MIT
pragma solidity ^0.8.29;

import { ISystemContract } from "@reactive/src/interfaces/ISystemContract.sol";

/// @title MockSystemContract — test double for the Reactive system contract.
/// @notice Etched onto the real system contract address (0x8888…8888) in tests so the
///         guardian's constructor subscriptions and callback requests can be recorded
///         and asserted locally, with no network involved.
contract MockSystemContract is ISystemContract {
    struct Subscription {
        address subscriber;
        uint256 chainId;
        address contractAddress;
        uint256 topic0;
        uint256 topic1;
        uint256 topic2;
        uint256 topic3;
    }

    struct CallbackRequestRecord {
        address sender;
        uint256 chainId;
        address recipient;
        uint64 gasLimit;
        bytes payload;
    }

    Subscription[] public subscriptions;
    CallbackRequestRecord[] public callbackRequests;

    function subscribe(
        uint256 chainId_,
        address contract_,
        uint256 topic0_,
        uint256 topic1_,
        uint256 topic2_,
        uint256 topic3_
    ) external {
        subscriptions.push(
            Subscription({
                subscriber: msg.sender,
                chainId: chainId_,
                contractAddress: contract_,
                topic0: topic0_,
                topic1: topic1_,
                topic2: topic2_,
                topic3: topic3_
            })
        );
    }

    function unsubscribe(uint256, address, uint256, uint256, uint256, uint256) external {
        // Not exercised by the guardian in this prep; present to satisfy the interface.
    }

    function requestCallback(CallbackVersion version_, bytes memory config_) external {
        require(version_ == CallbackVersion.V_1_0, "mock: bad version");
        CallbackConfiguration_V_1_0 memory config =
            abi.decode(config_, (CallbackConfiguration_V_1_0));
        _record(config);
    }

    function requestCallbackV_1_0(CallbackConfiguration_V_1_0 memory config_) external {
        _record(config_);
    }

    function debt(address) external pure returns (uint256) {
        return 0;
    }

    receive() external payable {}

    function subscriptionCount() external view returns (uint256) {
        return subscriptions.length;
    }

    function callbackCount() external view returns (uint256) {
        return callbackRequests.length;
    }

    function getCallback(uint256 i)
        external
        view
        returns (address sender, uint256 chainId, address recipient, uint64 gasLimit, bytes memory payload)
    {
        CallbackRequestRecord storage r = callbackRequests[i];
        return (r.sender, r.chainId, r.recipient, r.gasLimit, r.payload);
    }

    function _record(CallbackConfiguration_V_1_0 memory config_) internal {
        callbackRequests.push(
            CallbackRequestRecord({
                sender: msg.sender,
                chainId: config_.chainId,
                recipient: config_.recipient,
                gasLimit: config_.gasLimit,
                payload: config_.payload
            })
        );
    }
}
