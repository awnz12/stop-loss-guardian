// SPDX-License-Identifier: MIT
pragma solidity ^0.8.29;

import { Test } from "forge-std/Test.sol";
import { IAISignal } from "../src/interfaces/IAISignal.sol";
import { AISignalRegistry } from "../src/origin/AISignalRegistry.sol";

contract AISignalRegistryTest is Test {
    uint256 internal constant AI_KEY = 0xA11CE;
    uint256 internal constant OTHER_KEY = 0xBAD5EED;

    address internal ai;
    address internal token;
    AISignalRegistry internal registry;

    function setUp() public {
        ai = vm.addr(AI_KEY);
        token = makeAddr("token");
        registry = new AISignalRegistry(ai);
    }

    function testDirectUpdateByOperator() public {
        vm.expectEmit(true, false, false, true, address(registry));
        emit IAISignal.SignalUpdated(token, 100, true, 1);
        vm.prank(ai);
        registry.updateSignal(token, 100, true);

        IAISignal.Signal memory s = registry.getSignal(token);
        assertEq(s.triggerPrice, 100);
        assertTrue(s.armed);
        assertEq(s.nonce, 1);
        assertEq(s.updatedAt, block.timestamp);
    }

    function testDirectUpdateByStrangerReverts() public {
        vm.prank(makeAddr("stranger"));
        vm.expectRevert(abi.encodeWithSelector(AISignalRegistry.NotAiOperator.selector));
        registry.updateSignal(token, 100, true);
    }

    function testSignedUpdateRelayedByAnyone() public {
        uint256 deadline = block.timestamp + 1 hours;
        bytes memory sig = _sign(AI_KEY, token, 90, true, 1, deadline);

        // A random relayer submits the AI's signature — the AI itself never sends a tx.
        vm.prank(makeAddr("relayer"));
        registry.updateSignalSigned(token, 90, true, deadline, sig);

        IAISignal.Signal memory s = registry.getSignal(token);
        assertEq(s.triggerPrice, 90);
        assertTrue(s.armed);
        assertEq(s.nonce, 1);
    }

    function testSignedUpdateWrongSignerReverts() public {
        uint256 deadline = block.timestamp + 1 hours;
        bytes memory sig = _sign(OTHER_KEY, token, 90, true, 1, deadline);
        vm.expectRevert(abi.encodeWithSelector(AISignalRegistry.BadSignature.selector));
        registry.updateSignalSigned(token, 90, true, deadline, sig);
    }

    function testSignedUpdateReplayReverts() public {
        uint256 deadline = block.timestamp + 1 hours;
        bytes memory sig = _sign(AI_KEY, token, 90, true, 1, deadline);
        registry.updateSignalSigned(token, 90, true, deadline, sig);
        // Same signature again: the expected nonce has moved to 2, so the digest no
        // longer matches what was signed and recovery yields a different address.
        vm.expectRevert(abi.encodeWithSelector(AISignalRegistry.BadSignature.selector));
        registry.updateSignalSigned(token, 90, true, deadline, sig);
    }

    function testSignedUpdateExpiredReverts() public {
        bytes memory sig = _sign(AI_KEY, token, 90, true, 1, 0);
        vm.expectRevert(abi.encodeWithSelector(AISignalRegistry.SignatureExpired.selector, 0));
        registry.updateSignalSigned(token, 90, true, 0, sig);
    }

    function testOwnerRotatesOperator() public {
        address newAi = makeAddr("newAi");
        registry.setAiOperator(newAi);
        assertEq(registry.aiOperator(), newAi);

        // Old operator lost write access.
        vm.prank(ai);
        vm.expectRevert(abi.encodeWithSelector(AISignalRegistry.NotAiOperator.selector));
        registry.updateSignal(token, 1, true);

        // New operator can write.
        vm.prank(newAi);
        registry.updateSignal(token, 1, true);
        assertEq(registry.getSignal(token).triggerPrice, 1);
    }

    function testRotateByNonOwnerReverts() public {
        vm.prank(makeAddr("stranger"));
        vm.expectRevert(abi.encodeWithSelector(AISignalRegistry.NotOwner.selector));
        registry.setAiOperator(makeAddr("newAi"));
    }

    function _sign(
        uint256 key,
        address token_,
        uint256 triggerPrice,
        bool armed,
        uint256 nonce,
        uint256 deadline
    ) internal view returns (bytes memory) {
        bytes32 structHash = keccak256(
            abi.encode(registry.SIGNAL_TYPEHASH(), token_, triggerPrice, armed, nonce, deadline)
        );
        bytes32 digest = keccak256(abi.encodePacked("\x19\x01", registry.DOMAIN_SEPARATOR(), structHash));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(key, digest);
        return abi.encodePacked(r, s, v);
    }
}
