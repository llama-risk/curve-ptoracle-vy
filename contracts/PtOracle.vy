# pragma version 0.4.3
# pragma optimize gas
# pragma nonreentrancy on
# @license BUSL-1.1

from ethereum.ercs import IERC165

implements: IERC165

from pcaversaccio.snekmate.src.snekmate.auth import access_control

initializes: access_control

exports: (access_control.IERC165, access_control.IAccessControl)

# Interface for Principal Token example pt token: 0x6d98a2b6cdbf44939362a3e99793339ba2016af4
interface IPendlePT:
    def expiry() -> uint256: view


# Interface for underlying oracle
interface IOracle:
    def price() -> uint256: view


# Constants
DISCOUNT_PRECISION: constant(uint256) = 10**18
SECONDS_PER_YEAR: constant(uint256) = 365 * 24 * 60 * 60  # 31,536,000 seconds

# Role constants
MANAGER_ROLE: public(constant(bytes32)) = keccak256("MANAGER_ROLE")
PARAMETER_ADMIN_ROLE: public(constant(bytes32)) = keccak256(
    "PARAMETER_ADMIN_ROLE"
)
ADMIN_ROLE: public(constant(bytes32)) = keccak256("ADMIN_ROLE")

# State variables
pt: public(immutable(IPendlePT))
underlying_oracle: public(immutable(IOracle))
slope: public(uint256)  # Linear discount slope with 1e18 precision
intercept: public(uint256)  # Linear discount intercept with 1e18 precision
min_update_interval: public(uint256)
pt_expiry: public(immutable(uint256))

# Limit variables for slope and intercept changes (0 = no limit)
max_slope_change: public(uint256)  # Maximum allowed change in slope per update
max_intercept_change: public(
    uint256
)  # Maximum allowed change in intercept per update

# Price storage
last_update: public(uint256)
last_price: public(uint256)

# Rate limiting for set_linear_discount
last_discount_update: public(uint256)

# Events
event LinearDiscountUpdated:
    new_slope: indexed(uint256)
    new_intercept: indexed(uint256)


event LimitsUpdated:
    new_min_update_interval: uint256
    new_max_slope_change: uint256
    new_max_intercept_change: uint256


event PriceUpdated:
    new_price: indexed(uint256)


event OracleInitialized:
    pt: indexed(IPendlePT)
    underlying_oracle: indexed(IOracle)


# Constructor
@deploy
def __init__(
    _pt: IPendlePT,
    _underlying_oracle: IOracle,
    _slope: uint256,
    _intercept: uint256,
    _min_update_interval: uint256,
    _manager: address,
    _parameter_admin: address,
    _admin: address,
):
    # Validate all addresses are non-zero
    assert _pt != empty(IPendlePT), "invalid PT address"
    assert _underlying_oracle != empty(IOracle), "invalid oracle address"
    assert _manager != empty(address), "invalid manager address"
    assert _parameter_admin != empty(address), "invalid parameter admin address"
    assert _admin != empty(address), "invalid admin address"

    # Validate initial parameters
    assert _slope <= DISCOUNT_PRECISION, "initial slope exceeds precision"
    assert (
        _intercept <= DISCOUNT_PRECISION
    ), "initial intercept exceeds precision"

    # Initialize access control (msg.sender becomes default admin)
    access_control.__init__()

    # Set ADMIN_ROLE as the admin for MANAGER_ROLE and PARAMETER_ADMIN_ROLE
    access_control._set_role_admin(MANAGER_ROLE, ADMIN_ROLE)
    access_control._set_role_admin(PARAMETER_ADMIN_ROLE, ADMIN_ROLE)

    # Grant roles
    access_control._grant_role(MANAGER_ROLE, _manager)
    access_control._grant_role(PARAMETER_ADMIN_ROLE, _parameter_admin)
    access_control._grant_role(ADMIN_ROLE, _admin)

    # Revoke default admin role from deployer
    access_control._revoke_role(access_control.DEFAULT_ADMIN_ROLE, msg.sender)

    pt = _pt
    underlying_oracle = _underlying_oracle
    self.slope = _slope
    self.intercept = _intercept
    self.min_update_interval = _min_update_interval
    self.max_slope_change = max_value(
        uint256
    )  # Initialize to max value (no limit)
    self.max_intercept_change = max_value(
        uint256
    )  # Initialize to max value (no limit)
    self.last_update = block.timestamp
    self.last_discount_update = (
        block.timestamp
    )  # Initialize discount update timestamp
    pt_expiry = staticcall _pt.expiry()

    
    initial_price: uint256 = self._calculate_price()

    self.last_price = initial_price

    # Emit initialization event
    log OracleInitialized(pt=_pt, underlying_oracle=_underlying_oracle)

    log LinearDiscountUpdated(
        new_slope=_slope,
        new_intercept=_intercept,
    )

    log PriceUpdated(
        new_price=initial_price,
    )


# Internal functions
@internal
@view
def _calculate_price() -> uint256:

    # Get underlying oracle price and apply discount
    underlying_price: uint256 = staticcall underlying_oracle.price()

    if pt_expiry <= block.timestamp:
        return underlying_price

    discount: uint256 = self._calculate_discount(self.slope, self.intercept)

    # Calculate discount factor (between 0 and 1 with 1e18 precision)
    discount_factor: uint256 = DISCOUNT_PRECISION - discount

    # Return discounted price: underlying_price * discount_factor / DISCOUNT_PRECISION
    return (underlying_price * discount_factor) // DISCOUNT_PRECISION

@internal
@view
def _calculate_discount(slope_: uint256, intercept_: uint256)-> uint256:
    time_to_maturity_seconds: uint256 = pt_expiry - block.timestamp

    # Convert time to maturity to years with 1e18 precision
    time_to_maturity_years: uint256 = (
        time_to_maturity_seconds * DISCOUNT_PRECISION
    ) // SECONDS_PER_YEAR

    # Linear discount with 1e18 precision: discount = (slope * time_to_maturity_years) / 1e18 + intercept
    discount: uint256 = (
        slope_ * time_to_maturity_years
    ) // DISCOUNT_PRECISION + intercept_

    return discount


@view
@external
def price() -> uint256:
    """
    @notice Returns the current PT price using the underlying oracle and the latest discount parameters.
    @return The freshly computed discounted price of the PT.
    """
    return self._calculate_price()


# Write functions - All callers
@external
@nonpayable
def price_w() -> uint256:
    """
    @notice Returns the updated PT price and updates stored price cache.
    @dev If called multiple times in the same block, returns the cached value to save gas.
    @return The PT price after applying discount logic. If already updated this block, returns cached price.
    """

    # If PT has expired, return underlying oracle price
    if pt_expiry <= block.timestamp:
        return staticcall underlying_oracle.price()

    if block.timestamp == self.last_update:
        return self.last_price

    new_price: uint256 = self._calculate_price()

    # Update price and timestamp
    self.last_price = new_price
    self.last_update = block.timestamp

    # Emit price update event
    log PriceUpdated(new_price=new_price)

    return new_price


# Internal function to update discount parameters
@internal
def _update_discount_params(_slope: uint256, _intercept: uint256):

    # Cache storage reads to avoid multiple SLOADs
    old_slope: uint256 = self.slope
    old_intercept: uint256 = self.intercept
    
    # Validate slope and intercept bounds to prevent extreme discounts
    assert _slope <= DISCOUNT_PRECISION, "slope exceeds precision"
    assert _intercept <= DISCOUNT_PRECISION, "intercept exceeds precision"

    # Check slope change limit
    slope_change: uint256 = 0
    if _slope > old_slope:
        slope_change = _slope - old_slope
    else:
        slope_change = old_slope - _slope
    assert (slope_change <= self.max_slope_change), "slope change exceeds limit"

    intercept_change: uint256 = 0
    if _intercept > old_intercept:
        intercept_change = _intercept - old_intercept
    else:
        intercept_change = old_intercept - _intercept
    assert (
        intercept_change <= self.max_intercept_change
    ), "intercept change exceeds limit"

    new_discount: uint256= self._calculate_discount(_slope,_intercept)
    assert new_discount < DISCOUNT_PRECISION, "new discount exceeds precision"

    self.slope = _slope
    self.intercept = _intercept
    self.last_discount_update = block.timestamp

    log LinearDiscountUpdated(
        new_slope=_slope,
        new_intercept=_intercept,
    )


# Write functions - Manager only
@external
@nonpayable
def set_linear_discount(_slope: uint256, _intercept: uint256):
    """
    @notice Updates the linear discount parameters (slope and intercept).
    @param _slope The new slope value with 1e18 precision.
    @param _intercept The new intercept value with 1e18 precision.
    @dev Can only be called by an account with MANAGER_ROLE.
    @dev Enforced by rate limiting: updates are only allowed after min_update_interval since the last discount update.
    @dev Also validates that slope and intercept changes do not exceed max_slope_change and max_intercept_change.
    """
    access_control._check_role(MANAGER_ROLE, msg.sender)

    # Check rate limiting - Manager can only update once per min_update_interval
    assert (
        block.timestamp > self.last_discount_update + self.min_update_interval
    ), "update interval not elapsed"

    self._update_discount_params(_slope, _intercept)


@external
@nonpayable
def set_slope_from_apy(expected_apy: uint256):
    """
    @notice Set slope based on expected APY, with intercept set to 0
    @param expected_apy Expected APY with 1e18 precision (e.g., 5% = 5e16)
    """
    access_control._check_role(MANAGER_ROLE, msg.sender)

    # Check rate limiting - Manager can only update once per min_update_interval
    assert (
        block.timestamp > self.last_discount_update + self.min_update_interval
    ), "update interval not elapsed"

    # Validate APY is within reasonable bounds (e.g., max 1000% APY)
    assert expected_apy <= 10 * DISCOUNT_PRECISION, "APY exceeds maximum bound"
    assert expected_apy > 0, "APY must be positive"

    # For linear discount model with yearly slope:
    # If we want an APY of X%, the slope should be X% per year
    # Since time_to_maturity is now in years with 1e18 precision,
    # and discount = (slope * time_to_maturity_years) / 1e18
    # We want: discount_per_year = expected_apy / (1 + expected_apy)

    # Calculate the discount rate from APY
    # discount_rate = APY / (1 + APY)
    numerator: uint256 = expected_apy * DISCOUNT_PRECISION
    denominator: uint256 = DISCOUNT_PRECISION + expected_apy

    # Calculate slope (discount per year with 1e18 precision)
    new_slope: uint256 = numerator // denominator

    self._update_discount_params(new_slope, 0)


# Write functions - Parameter Admin only
@external
@nonpayable
def set_limits(
    _min_update_interval: uint256,
    _max_slope_change: uint256,
    _max_intercept_change: uint256,
):
    """
    @notice Updates configuration limits governing discount updates.
    @param _min_update_interval Minimum allowed time (in seconds) between manager discount updates.
    @param _max_slope_change Maximum allowed change in slope per update (0 = no limit).
    @param _max_intercept_change Maximum allowed change in intercept per update (0 = no limit).
    @dev Only callable by accounts with PARAMETER_ADMIN_ROLE.
    @dev Setting a limit to zero or max(uint256) effectively removes restrictions.
    """
    access_control._check_role(PARAMETER_ADMIN_ROLE, msg.sender)

    self.min_update_interval = _min_update_interval
    self.max_slope_change = _max_slope_change
    self.max_intercept_change = _max_intercept_change

    log LimitsUpdated(
        new_min_update_interval=_min_update_interval,
        new_max_slope_change=_max_slope_change,
        new_max_intercept_change=_max_intercept_change,
    )
