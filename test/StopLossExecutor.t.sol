// SPDX-License-Identifier: MIT
pragma solidity ^0.8.29;

import { Test } from "forge-std/Test.sol";
import { IPayable } from "@reactive/src/interfaces/IPayable.sol";
import { AbstractPayer } from "@reactive/src/base/AbstractPayer.sol";
import { AbstractCallback } from "@reactive/src/base/AbstractCallback.sol";
import { MockPositionVault } from "../src/destination/MockPositionVault.sol";
import { StopLossExecutor } from "../src/destination/StopLossExecutor.sol";

contract StopLossExecutorTest is Test {
    // The real Base Sepolia callback proxy from the official docs table.
    address payable internal constant PROXY = payable(0x476F44b11F5C942c58f4c8E3d0300E5710743585);

    MockPositionVault internal vault;
    StopLossExecutor internal executor;
    address internal guardian;
    address internal token;
    address internal userA;
    address internal userB;

    function setUp() public {
        guardian = makeAddr("guardian");
        token = makeAddr("token");
        userA = makeAddr("userA");
        userB = makeAddr("userB");

        vault = new MockPositionVault();
        executor = new StopLossExecutor(IPayable(PROXY), guardian, vault);
        vault.setExecutor(address(executor));

        vm.prank(userA);
        vault.openPosition(token, 1_000);
        vm.prank(userB);
        vault.openPosition(token, 500);
    }

    function testCallbackExitsAllPositions() public {
        vm.expectEmit(true, false, false, true, address(executor));
        emit StopLossExecutor.StopLossExecuted(token, 90, 2);
        vm.prank(PROXY);
        executor.exitPosition(guardian, token, 90);

        (uint256 amountA, bool openA) = vault.positions(userA, token);
        (uint256 amountB, bool openB) = vault.positions(userB, token);
        assertEq(amountA, 1_000);
        assertFalse(openA);
        assertEq(amountB, 500);
        assertFalse(openB);
    }

    function testCallbackFromNonProxyReverts() public {
        address caller = makeAddr("mallory");
        vm.prank(caller);
        vm.expectRevert(
            abi.encodeWithSelector(AbstractPayer.NotAuthorized.selector, caller, PROXY)
        );
        executor.exitPosition(guardian, token, 90);
    }

    function testCallbackWithWrongReactiveSenderReverts() public {
        address impostor = makeAddr("impostorGuardian");
        vm.prank(PROXY);
        vm.expectRevert(
            abi.encodeWithSelector(
                AbstractCallback.CallbackNotAuthorized.selector, impostor, guardian
            )
        );
        executor.exitPosition(impostor, token, 90);
    }

    function testVaultExitDirectlyByNonExecutorReverts() public {
        vm.prank(userA);
        vm.expectRevert(abi.encodeWithSelector(MockPositionVault.NotExecutor.selector));
        vault.exitAllForToken(token, 90);
    }

    function testCallbackWithNoOpenPositionsIsHarmless() public {
        address emptyToken = makeAddr("emptyToken");
        vm.expectEmit(true, false, false, true, address(executor));
        emit StopLossExecutor.StopLossExecuted(emptyToken, 1, 0);
        vm.prank(PROXY);
        executor.exitPosition(guardian, emptyToken, 1);
    }
}
