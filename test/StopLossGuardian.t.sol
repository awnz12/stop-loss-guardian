// SPDX-License-Identifier: MIT
pragma solidity ^0.8.29;

import { Test } from "forge-std/Test.sol";
import { IReactive } from "@reactive/src/interfaces/IReactive.sol";
import { AbstractPayer } from "@reactive/src/base/AbstractPayer.sol";
import { MockPriceFeed } from "../src/origin/MockPriceFeed.sol";
import { AISignalRegistry } from "../src/origin/AISignalRegistry.sol";
import { StopLossGuardian } from "../src/reactive/StopLossGuardian.sol";
import { MockSystemContract } from "./MockSystemContract.sol";

contract StopLossGuardianTest is Test {
    address internal constant SYSTEM_ADDR = 0x8888888888888888888888888888888888888888;
    uint256 internal constant ORIGIN = 84532; // Base Sepolia (origin and destination in the demo)

    MockPriceFeed internal feed;
    AISignalRegistry internal registry;
    StopLossGuardian internal guardian;
    MockSystemContract internal system;
    address internal token;
    address internal executor;

    function setUp() public {
        token = makeAddr("token");
        executor = makeAddr("executor");
        feed = new MockPriceFeed();
        registry = new AISignalRegistry(makeAddr("aiOperator"));

        // Put the recording mock where the real system contract lives, BEFORE the
        // guardian is deployed, so its constructor subscriptions are captured.
        MockSystemContract impl = new MockSystemContract();
        vm.etch(SYSTEM_ADDR, address(impl).code);
        system = MockSystemContract(payable(SYSTEM_ADDR));

        guardian = new StopLossGuardian(ORIGIN, address(feed), address(registry), ORIGIN);
        guardian.setRecipient(executor);
    }

    function testConstructorSubscribesToBothEvents() public view {
        assertEq(system.subscriptionCount(), 2);

        (address sub0, uint256 chain0, address contract0, uint256 topic0,,,) = system.subscriptions(0);
        assertEq(sub0, address(guardian));
        assertEq(chain0, ORIGIN);
        assertEq(contract0, address(feed));
        assertEq(topic0, guardian.PRICE_UPDATED_TOPIC());

        (address sub1, uint256 chain1, address contract1, uint256 topic1,,,) = system.subscriptions(1);
        assertEq(sub1, address(guardian));
        assertEq(chain1, ORIGIN);
        assertEq(contract1, address(registry));
        assertEq(topic1, guardian.SIGNAL_UPDATED_TOPIC());
    }

    function testReactRevertsForNonSystemCaller() public {
        // Build the log first: _priceLog() makes an external getter call, which would
        // otherwise consume the expectRevert.
        IReactive.LogRecord memory log = _priceLog(50);
        vm.expectRevert(
            abi.encodeWithSelector(AbstractPayer.NotAuthorized.selector, address(this), SYSTEM_ADDR)
        );
        guardian.react(log);
    }

    function testSignalArmsGuardian() public {
        vm.expectEmit(true, false, false, true, address(guardian));
        emit StopLossGuardian.GuardianArmed(token, 100);
        _react(_signalLog(100, true, 1));

        (uint256 trigger, bool armed, bool triggered) = guardian.guards(token);
        assertEq(trigger, 100);
        assertTrue(armed);
        assertFalse(triggered);
    }

    function testPriceAboveTriggerDoesNothing() public {
        _react(_signalLog(100, true, 1));
        _react(_priceLog(150));
        assertEq(system.callbackCount(), 0);
        (,, bool triggered) = guardian.guards(token);
        assertFalse(triggered);
    }

    function testPriceAtTriggerRequestsCallback() public {
        _react(_signalLog(100, true, 1));

        vm.expectEmit(true, false, false, true, address(guardian));
        emit StopLossGuardian.StopLossTriggered(token, 100, 100);
        _react(_priceLog(100));

        assertEq(system.callbackCount(), 1);
        (address sender, uint256 chainId, address recipient, uint64 gasLimit, bytes memory payload) =
            system.getCallback(0);
        assertEq(sender, address(guardian));
        assertEq(chainId, ORIGIN);
        assertEq(recipient, executor);
        assertGt(gasLimit, 0);
        // Payload: exitPosition(address(0) reserved, token, price) — the network will
        // overwrite the reserved address with the guardian's address on delivery.
        bytes memory expected =
            abi.encodeWithSignature("exitPosition(address,address,uint256)", address(0), token, uint256(100));
        assertEq(payload, expected);
    }

    function testLatchPreventsRefireUntilRearmed() public {
        _react(_signalLog(100, true, 1));
        _react(_priceLog(90));
        _react(_priceLog(80));
        assertEq(system.callbackCount(), 1);

        // A fresh AI signal re-arms the latch.
        _react(_signalLog(100, true, 2));
        _react(_priceLog(70));
        assertEq(system.callbackCount(), 2);
    }

    function testDisarmedIgnoresPrice() public {
        _react(_signalLog(100, true, 1));
        _react(_signalLog(100, false, 2));
        _react(_priceLog(10));
        assertEq(system.callbackCount(), 0);
    }

    function testSignalFromWrongContractIgnored() public {
        IReactive.LogRecord memory log = _signalLog(100, true, 1);
        log.contractAddress = address(feed); // right topic, wrong source contract
        _react(log);
        (uint256 trigger, bool armed,) = guardian.guards(token);
        assertEq(trigger, 0);
        assertFalse(armed);
    }

    function testSetRecipientOnlyOwner() public {
        vm.prank(makeAddr("stranger"));
        vm.expectRevert(abi.encodeWithSelector(StopLossGuardian.NotOwner.selector));
        guardian.setRecipient(makeAddr("other"));

        address next = makeAddr("nextExecutor");
        guardian.setRecipient(next);
        assertEq(guardian.recipient(), next);
    }

    // --- helpers ---

    function _react(IReactive.LogRecord memory log) internal {
        vm.prank(SYSTEM_ADDR);
        guardian.react(log);
    }

    function _signalLog(uint256 trigger, bool armed, uint256 nonce)
        internal
        view
        returns (IReactive.LogRecord memory)
    {
        return IReactive.LogRecord({
            chainId: ORIGIN,
            contractAddress: address(registry),
            topic0: guardian.SIGNAL_UPDATED_TOPIC(),
            topic1: uint256(uint160(token)),
            topic2: 0,
            topic3: 0,
            data: abi.encode(trigger, armed, nonce),
            blockNumber: 1,
            opCode: 2,
            blockHash: 0,
            txHash: 0,
            logIndex: 0
        });
    }

    function _priceLog(uint256 price) internal view returns (IReactive.LogRecord memory) {
        return IReactive.LogRecord({
            chainId: ORIGIN,
            contractAddress: address(feed),
            topic0: guardian.PRICE_UPDATED_TOPIC(),
            topic1: uint256(uint160(token)),
            topic2: 0,
            topic3: 0,
            data: abi.encode(price, uint256(1_700_000_000)),
            blockNumber: 2,
            opCode: 2,
            blockHash: 0,
            txHash: 0,
            logIndex: 0
        });
    }
}
