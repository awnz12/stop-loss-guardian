// SPDX-License-Identifier: MIT
pragma solidity ^0.8.29;

import { ISystemContract } from "@reactive/src/interfaces/ISystemContract.sol";
import { IReactive } from "@reactive/src/interfaces/IReactive.sol";
import { AbstractReactive } from "@reactive/src/base/AbstractReactive.sol";

/// @title StopLossGuardian — the reactive brain of "Penjaga Stop-Loss Agentik".
/// @notice Deployed on Reactive Network (Lasna testnet). It subscribes to two event streams
///         on the origin chain (Base Sepolia for the demo):
///           1. `SignalUpdated` from the AISignalRegistry — the AI agent arming/updating
///              stop-loss parameters. The guardian mirrors them into its own state, because
///              reactive contracts learn about other chains only through logs.
///           2. `PriceUpdated` from the MockPriceFeed — market movement.
///         When a price lands at or below the armed trigger, the guardian latches (fires at
///         most once per arming) and requests a callback to the StopLossExecutor on the
///         destination chain, which closes the protected positions. No human, no keeper bot.
/// @dev Follows the Reactive Omni pattern: `onlySystem` on `react()`, subscriptions via
///      `SYSTEM.subscribe`, callbacks via `SYSTEM.requestCallbackV_1_0` with the first
///      payload argument reserved (`address(0)`) for sender injection by the network.
contract StopLossGuardian is AbstractReactive {
    /// @notice topic0 of MockPriceFeed.PriceUpdated(address,uint256,uint256)
    uint256 public constant PRICE_UPDATED_TOPIC =
        uint256(keccak256("PriceUpdated(address,uint256,uint256)"));

    /// @notice topic0 of IAISignal.SignalUpdated(address,uint256,bool,uint256)
    uint256 public constant SIGNAL_UPDATED_TOPIC =
        uint256(keccak256("SignalUpdated(address,uint256,bool,uint256)"));

    /// @notice Gas limit forwarded to the destination callback.
    uint64 private constant CALLBACK_GAS_LIMIT = 1_000_000;

    /// @notice Mirrored stop-loss state for one token.
    struct GuardState {
        uint256 triggerPrice;
        bool armed;
        bool triggered; // latch: true after firing, reset by the next SignalUpdated
    }

    /// @notice Deployer; may set the callback recipient and nothing else.
    address public owner;

    uint256 public immutable originChainId;
    address public immutable priceFeed;
    address public immutable signalRegistry;
    uint256 public immutable destinationChainId;

    /// @notice StopLossExecutor address on the destination chain. Set after deployment
    ///         (the executor needs this contract's address first — see docs deployment order).
    address public recipient;

    mapping(address => GuardState) public guards;

    event GuardianArmed(address indexed token, uint256 triggerPrice);
    event GuardianDisarmed(address indexed token);
    event StopLossTriggered(address indexed token, uint256 price, uint256 triggerPrice);
    event RecipientSet(address indexed recipient);

    error NotOwner();
    error RecipientNotSet();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    /// @param originChainId_ Chain the price feed and signal registry live on (Base Sepolia: 84532).
    /// @param priceFeed_ MockPriceFeed address on the origin chain.
    /// @param signalRegistry_ AISignalRegistry address on the origin chain.
    /// @param destinationChainId_ Chain the callback is delivered to (Base Sepolia: 84532).
    constructor(
        uint256 originChainId_,
        address priceFeed_,
        address signalRegistry_,
        uint256 destinationChainId_
    ) payable {
        owner = msg.sender;
        originChainId = originChainId_;
        priceFeed = priceFeed_;
        signalRegistry = signalRegistry_;
        destinationChainId = destinationChainId_;

        SYSTEM.subscribe(
            originChainId_, priceFeed_, PRICE_UPDATED_TOPIC, REACTIVE_IGNORE, REACTIVE_IGNORE, REACTIVE_IGNORE
        );
        SYSTEM.subscribe(
            originChainId_,
            signalRegistry_,
            SIGNAL_UPDATED_TOPIC,
            REACTIVE_IGNORE,
            REACTIVE_IGNORE,
            REACTIVE_IGNORE
        );
    }

    /// @notice Point the guardian at its executor on the destination chain (owner only).
    function setRecipient(address recipient_) external onlyOwner {
        recipient = recipient_;
        emit RecipientSet(recipient_);
    }

    /// @notice Settle outstanding debt to the system contract from this contract's balance.
    function coverDebt() external {
        _coverDebt();
    }

    /// @inheritdoc IReactive
    function react(LogRecord memory log_) external onlySystem {
        if (log_.topic0 == SIGNAL_UPDATED_TOPIC && log_.contractAddress == signalRegistry) {
            _onSignal(log_);
        } else if (log_.topic0 == PRICE_UPDATED_TOPIC && log_.contractAddress == priceFeed) {
            _onPrice(log_);
        }
        // Non-matching logs fall through silently — reverting as a filter would still be
        // billed by the system contract (see docs: "Reacting To Events").
    }

    /// @dev Mirror the AI's latest parameters. Any new signal re-arms the latch.
    function _onSignal(LogRecord memory log_) internal {
        address token = address(uint160(log_.topic1));
        (uint256 triggerPrice, bool armed,) = abi.decode(log_.data, (uint256, bool, uint256));
        GuardState storage g = guards[token];
        g.triggerPrice = triggerPrice;
        g.armed = armed;
        g.triggered = false;
        if (armed) {
            emit GuardianArmed(token, triggerPrice);
        } else {
            emit GuardianDisarmed(token);
        }
    }

    /// @dev Evaluate a price print against the mirrored trigger; fire at most once per arming.
    function _onPrice(LogRecord memory log_) internal {
        address token = address(uint160(log_.topic1));
        (uint256 price,) = abi.decode(log_.data, (uint256, uint256));
        GuardState storage g = guards[token];
        if (!g.armed || g.triggered) return;
        if (price > g.triggerPrice) return;
        if (recipient == address(0)) revert RecipientNotSet();
        g.triggered = true;
        emit StopLossTriggered(token, price, g.triggerPrice);

        // First payload argument is reserved: the network overwrites it with this
        // contract's address so the executor can authenticate the callback sender.
        bytes memory payload = abi.encodeWithSignature(
            "exitPosition(address,address,uint256)", address(0), token, price
        );
        SYSTEM.requestCallbackV_1_0(
            ISystemContract.CallbackConfiguration_V_1_0({
                chainId: destinationChainId,
                recipient: recipient,
                gasLimit: CALLBACK_GAS_LIMIT,
                payload: payload
            })
        );
    }
}
