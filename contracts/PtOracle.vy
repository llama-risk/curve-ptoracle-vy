# pragma version 0.4.3
# pragma optimize gas
# @license MIT
# Security: Nonreentrancy is enabled by default in Vyper 0.4.3 for all external functions

# Interface for Principal Token example pt token: 0x6d98a2b6cdbf44939362a3e99793339ba2016af4
interface PendlePT:
    def expiry() -> uint256: view


# Interface for underlying oracle
interface Oracle:
    def price() -> uint256: view


# Constants
DISCOUNT_PRECISION: constant(uint256) = 10**18

# State variables
pt: public(address)
underlying_oracle: public(address)
slope: public(uint256)  # Linear discount slope with 1e18 precision
intercept: public(uint256)  # Linear discount intercept with 1e18 precision
max_update_interval: public(uint256)
pt_expiry: public(immutable(uint256))

# Access control
manager: public(address)
admin: public(address)

# Modifiers
@internal
def _only_manager():
    assert msg.sender == self.manager, "caller is not manager"


@internal
def _only_admin():
    assert msg.sender == self.admin, "caller is not admin"


# Price storage
last_update: public(uint256)
last_price: public(uint256)

# Rate limiting for set_linear_discount
last_discount_update: public(uint256)

# Events
event LinearDiscountUpdated:
    old_slope: indexed(uint256)
    old_intercept: indexed(uint256)
    new_slope: uint256
    new_intercept: uint256


event LimitsUpdated:
    old_max_update_interval: uint256
    new_max_update_interval: uint256


event PriceUpdated:
    old_price: indexed(uint256)
    new_price: indexed(uint256)
    timestamp: uint256


event OracleInitialized:
    pt: indexed(address)
    underlying_oracle: indexed(address)
    initial_slope: uint256
    initial_intercept: uint256


event ManagerUpdated:
    old_manager: indexed(address)
    new_manager: indexed(address)


# Constructor
@deploy
def __init__(
    _pt: address,
    _underlying_oracle: address,
    _slope: uint256,
    _intercept: uint256,
    _max_update_interval: uint256,
    _manager: address,
    _admin: address,
):
    # Validate all addresses are non-zero
    assert _pt != empty(address), "invalid PT address"
    assert _underlying_oracle != empty(address), "invalid oracle address"
    assert _manager != empty(address), "invalid manager address"
    assert _admin != empty(address), "invalid admin address"

    # Validate initial parameters
    assert _slope <= DISCOUNT_PRECISION, "initial slope exceeds precision"
    assert (
        _intercept <= DISCOUNT_PRECISION
    ), "initial intercept exceeds precision"
    assert _max_update_interval > 0, "invalid update interval"

    self.pt = _pt
    self.underlying_oracle = _underlying_oracle
    self.slope = _slope
    self.intercept = _intercept
    self.max_update_interval = _max_update_interval
    self.manager = _manager
    self.admin = _admin
    self.last_update = block.timestamp
    self.last_discount_update = (
        block.timestamp
    )  # Initialize discount update timestamp
    pt_expiry = staticcall PendlePT(_pt).expiry()

    # Initialize price
    self.last_price = self._calculate_price()

    # Emit initialization event
    log OracleInitialized(
        pt=_pt,
        underlying_oracle=_underlying_oracle,
        initial_slope=_slope,
        initial_intercept=_intercept,
    )


# Internal functions
@internal
@view
def _calculate_price() -> uint256:

    # Get underlying oracle price and apply discount
    underlying_price: uint256 = staticcall Oracle(
        self.underlying_oracle
    ).price()

    if pt_expiry <= block.timestamp:
        return underlying_price

    time_to_maturity: uint256 = pt_expiry - block.timestamp

    # Linear discount with 1e18 precision: discount = slope * time_in_seconds + intercept
    discount: uint256 = self.slope * time_to_maturity + self.intercept

    # Ensure discount doesn't exceed precision (should never happen but critical for safety)
    assert discount <= DISCOUNT_PRECISION, "discount exceeds precision"

    # Calculate discount factor (between 0 and 1 with 1e18 precision)
    discount_factor: uint256 = DISCOUNT_PRECISION - discount

    # Return discounted price: underlying_price * discount_factor / DISCOUNT_PRECISION
    return (underlying_price * discount_factor) // DISCOUNT_PRECISION


# View functions
@view
@external
def price() -> uint256:
    return self._calculate_price()


# Write functions - All callers
@external
@nonpayable
def price_w() -> uint256:
    # If PT has expired, return underlying oracle price
    if pt_expiry <= block.timestamp:
        return staticcall Oracle(self.underlying_oracle).price()


    # If timestamp is the same, just return current price (no update needed)
    if block.timestamp == self.last_update:
        return self.last_price


    # Calculate new price using linear discount model
    new_price: uint256 = self._calculate_price()

    # Store old price for event
    old_price: uint256 = self.last_price

    # Update price and timestamp
    self.last_price = new_price
    self.last_update = block.timestamp

    # Emit price update event
    log PriceUpdated(
        old_price=old_price, new_price=new_price, timestamp=block.timestamp
    )

    return new_price


# Internal function to update discount parameters
@internal
def _update_discount_params(_slope: uint256, _intercept: uint256):
    # Validate slope and intercept bounds to prevent extreme discounts
    assert _slope <= DISCOUNT_PRECISION, "slope exceeds precision"
    assert _intercept <= DISCOUNT_PRECISION, "intercept exceeds precision"

    # Store old values for event
    old_slope: uint256 = self.slope
    old_intercept: uint256 = self.intercept

    # Update parameters
    self.slope = _slope
    self.intercept = _intercept
    self.last_discount_update = block.timestamp

    log LinearDiscountUpdated(
        old_slope=old_slope,
        old_intercept=old_intercept,
        new_slope=_slope,
        new_intercept=_intercept,
    )


# Write functions - Manager only
@external
@nonpayable
def set_linear_discount(_slope: uint256, _intercept: uint256):
    self._only_manager()

    # Check rate limiting - Manager can only update once per max_update_interval
    assert (
        block.timestamp > self.last_discount_update + self.max_update_interval
    ), "update interval not elapsed"

    self._update_discount_params(_slope, _intercept)


@external
@nonpayable
def set_slope_from_apy(expected_apy: uint256):
    """
    @notice Set slope based on expected APY, with intercept set to 0
    @param expected_apy Expected APY with 1e18 precision
    """
    self._only_manager()

    # Check rate limiting - Manager can only update once per max_update_interval
    assert (
        block.timestamp > self.last_discount_update + self.max_update_interval
    ), "update interval not elapsed"

    # Constants
    SECONDS_PER_YEAR: uint256 = 365 * 24 * 60 * 60  # ~31,536,000 seconds

    # Validate APY is within reasonable bounds (e.g., max 1000% APY)
    assert expected_apy <= 10 * DISCOUNT_PRECISION, "APY exceeds maximum bound"
    assert expected_apy > 0, "APY must be positive"

    # Calculate numerator and denominator to avoid precision loss
    numerator: uint256 = expected_apy
    denominator: uint256 = (
        DISCOUNT_PRECISION + expected_apy
    ) * SECONDS_PER_YEAR

    # Ensure denominator won't cause division by zero
    assert denominator > 0, "invalid denominator"

    # Calculate slope with proper precision
    new_slope: uint256 = (numerator * DISCOUNT_PRECISION) // denominator

    self._update_discount_params(new_slope, 0)


# Write functions - Admin only
@external
@nonpayable
def set_manager(_new_manager: address) -> bool:
    """
    @notice Update the manager address (admin-only function)
    @param _new_manager The new manager address
    @return True on successful update
    """
    self._only_admin()

    # Validate new manager address is not zero
    assert _new_manager != empty(address), "invalid manager address"

    # Store old manager for event
    old_manager: address = self.manager

    # Update manager
    self.manager = _new_manager

    # Emit event
    log ManagerUpdated(old_manager=old_manager, new_manager=_new_manager)

    return True


@external
@nonpayable
def set_limits(_max_update_interval: uint256):
    self._only_admin()
    assert _max_update_interval > 0, "invalid update interval"

    # Store old value for event
    old_max_update_interval: uint256 = self.max_update_interval

    self.max_update_interval = _max_update_interval

    log LimitsUpdated(
        old_max_update_interval=old_max_update_interval,
        new_max_update_interval=_max_update_interval,
    )
