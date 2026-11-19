// SPDX-License-Identifier: MIT
pragma solidity ^0.8.29;

import { IAISignal } from "../interfaces/IAISignal.sol";

/// @title AISignalRegistry — where the AI agent posts stop-loss parameters on the origin chain.
/// @notice Deployed on the origin chain (Base Sepolia for the demo) next to the price feed.
///         Only the AI operator can write: directly, or via an EIP-712 signature that anyone
///         can relay. The owner (deployer) can rotate the operator and nothing else — the
///         owner cannot forge or alter signals.
contract AISignalRegistry is IAISignal {
    /// @notice EIP-712 typehash for a signed signal update.
    bytes32 public constant SIGNAL_TYPEHASH =
        keccak256("SignalUpdate(address token,uint256 triggerPrice,bool armed,uint256 nonce,uint256 deadline)");

    /// @notice EIP-712 domain separator, bound to this contract and chain at deployment.
    bytes32 public immutable DOMAIN_SEPARATOR;

    /// @notice Deployer; may rotate the AI operator, nothing more.
    address public owner;

    /// @inheritdoc IAISignal
    address public aiOperator;

    mapping(address => IAISignal.Signal) private _signals;

    event AiOperatorUpdated(address indexed previousOperator, address indexed newOperator);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    error NotOwner();
    error NotAiOperator();
    error BadSignature();
    error BadNonce(uint256 expected, uint256 provided);
    error SignatureExpired(uint256 deadline);

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor(address aiOperator_) {
        owner = msg.sender;
        aiOperator = aiOperator_;
        DOMAIN_SEPARATOR = keccak256(
            abi.encode(
                keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"),
                keccak256(bytes("AgenticStopLossSignal")),
                keccak256(bytes("1")),
                block.chainid,
                address(this)
            )
        );
    }

    /// @inheritdoc IAISignal
    function updateSignal(address token, uint256 triggerPrice, bool armed) external {
        if (msg.sender != aiOperator) revert NotAiOperator();
        _applyUpdate(token, triggerPrice, armed);
    }

    /// @inheritdoc IAISignal
    function updateSignalSigned(
        address token,
        uint256 triggerPrice,
        bool armed,
        uint256 deadline,
        bytes calldata signature
    ) external {
        if (block.timestamp > deadline) revert SignatureExpired(deadline);
        uint256 expectedNonce = _signals[token].nonce + 1;
        bytes32 structHash =
            keccak256(abi.encode(SIGNAL_TYPEHASH, token, triggerPrice, armed, expectedNonce, deadline));
        bytes32 digest = keccak256(abi.encodePacked("\x19\x01", DOMAIN_SEPARATOR, structHash));
        address signer = _recover(digest, signature);
        if (signer != aiOperator) revert BadSignature();
        _applyUpdate(token, triggerPrice, armed);
    }

    /// @inheritdoc IAISignal
    function getSignal(address token) external view returns (IAISignal.Signal memory) {
        return _signals[token];
    }

    /// @notice Rotate the AI operator. Only the owner; takes effect for both write paths.
    function setAiOperator(address newOperator) external onlyOwner {
        emit AiOperatorUpdated(aiOperator, newOperator);
        aiOperator = newOperator;
    }

    /// @notice Transfer ownership (operator-rotation rights).
    function transferOwnership(address newOwner) external onlyOwner {
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    function _applyUpdate(address token, uint256 triggerPrice, bool armed) internal {
        IAISignal.Signal storage s = _signals[token];
        s.triggerPrice = triggerPrice;
        s.armed = armed;
        s.nonce += 1;
        s.updatedAt = block.timestamp;
        emit SignalUpdated(token, triggerPrice, armed, s.nonce);
    }

    function _recover(bytes32 digest, bytes calldata signature) internal pure returns (address) {
        if (signature.length != 65) return address(0);
        bytes32 r;
        bytes32 s;
        uint8 v;
        // solhint-disable-next-line no-inline-assembly
        assembly {
            r := calldataload(signature.offset)
            s := calldataload(add(signature.offset, 32))
            v := byte(0, calldataload(add(signature.offset, 64)))
        }
        if (v < 27) v += 27;
        if (v != 27 && v != 28) return address(0);
        return ecrecover(digest, v, r, s);
    }
}
