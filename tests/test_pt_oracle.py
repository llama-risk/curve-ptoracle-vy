import pytest
import boa
from datetime import datetime, timedelta


@pytest.fixture
def owner():
    return boa.env.generate_address()


@pytest.fixture
def llamarisk():
    return boa.env.generate_address()


@pytest.fixture
def curve_dao():
    return boa.env.generate_address()


@pytest.fixture
def mock_pt(owner):
    """Deploy a mock PT token with expiry"""
    mock_pt_code = """
# pragma version 0.4.3

expiry: public(uint256)

@deploy
def __init__(_expiry: uint256):
    self.expiry = _expiry
"""
    with boa.env.prank(owner):
        # Set expiry to 30 days from now
        future_expiry = boa.env.evm.patch.timestamp + (30 * 24 * 60 * 60)
        return boa.loads(mock_pt_code, future_expiry)


@pytest.fixture
def mock_oracle(owner):
    """Deploy a mock underlying oracle"""
    mock_oracle_code = """
# pragma version 0.4.3

stored_price: uint256

@deploy
def __init__():
    self.stored_price = 10**18  # 1 ETH = $1 for simplicity

@view
@external
def price() -> uint256:
    return self.stored_price

@external
def set_price(_price: uint256):
    self.stored_price = _price
"""
    with boa.env.prank(owner):
        return boa.loads(mock_oracle_code)


@pytest.fixture
def pt_oracle(owner, llamarisk, curve_dao, mock_pt, mock_oracle):
    """Deploy the PtOracle contract"""
    from contracts import PtOracle
    
    with boa.env.prank(owner):
        return PtOracle.deploy(
            mock_pt.address,  # PT token
            mock_oracle.address,  # underlying oracle
            1,  # slope (very small slope)
            0,   # intercept (0% with 1e8 precision)
            86400,  # max_update_interval (24 hours)
            llamarisk,
            curve_dao
        )


class TestPtOracle:
    def test_deployment(self, pt_oracle, owner, llamarisk, curve_dao, mock_pt, mock_oracle):
        """Test that the contract deploys correctly with initial values"""
        assert pt_oracle.owner() == owner
        assert pt_oracle.llamarisk() == llamarisk
        assert pt_oracle.curve_dao() == curve_dao
        assert pt_oracle.pt() == mock_pt.address
        assert pt_oracle.underlying_oracle() == mock_oracle.address
        assert pt_oracle.slope() == 1
        assert pt_oracle.intercept() == 0
        assert pt_oracle.max_update_interval() == 86400
    
    def test_price_calculation(self, pt_oracle, mock_oracle):
        """Test that price() calculates the correct discounted price"""
        # The discount should be applied to the underlying price
        # With 30 days to maturity, slope=1, intercept=0
        # discount = (1 * 30*24*60*60) + 0 (in terms of 1e8 precision)
        price = pt_oracle.price()
        
        # Price should be less than underlying due to discount
        underlying_price = mock_oracle.price()
        assert price < underlying_price
        assert price > 0
    
    def test_price_w_caching(self, pt_oracle):
        """Test that price_w() caches the price correctly"""
        # First call to price_w
        price1 = pt_oracle.price_w()
        timestamp1 = pt_oracle.last_update()
        
        # Immediate second call (same block) should return same price without recalculation
        price2 = pt_oracle.price_w()
        timestamp2 = pt_oracle.last_update()
        
        assert price1 == price2
        assert timestamp1 == timestamp2
        assert pt_oracle.current_price() == price1
    
    def test_price_w_update(self, pt_oracle):
        """Test that price_w() updates when time passes"""
        # First call
        price1 = pt_oracle.price_w()
        
        # Advance time by 1 hour
        boa.env.evm.patch.timestamp += 3600
        
        # Second call should update
        price2 = pt_oracle.price_w()
        
        # Price should change slightly due to time passing (less time to maturity)
        assert price2 != price1
        assert pt_oracle.last_update() > 0
    
    def test_expired_pt_returns_zero(self, owner):
        """Test that expired PT returns 0 price"""
        from contracts import PtOracle
        
        # Create PT with past expiry
        mock_pt_code = """
# pragma version 0.4.3

expiry: public(uint256)

@deploy
def __init__(_expiry: uint256):
    self.expiry = _expiry
"""
        with boa.env.prank(owner):
            # Set expiry to past
            past_expiry = boa.env.evm.patch.timestamp - 1
            expired_pt = boa.loads(mock_pt_code, past_expiry)
            
            # Create mock oracle
            mock_oracle_code = """
# pragma version 0.4.3

@view
@external
def price() -> uint256:
    return 10**18
"""
            mock_oracle = boa.loads(mock_oracle_code)
            
            # Deploy PtOracle with expired PT
            pt_oracle = PtOracle.deploy(
                expired_pt.address,
                mock_oracle.address,
                1, 0, 86400,
                boa.env.generate_address(),
                boa.env.generate_address()
            )
            
            assert pt_oracle.price() == 0
            assert pt_oracle.price_w() == 0
    
    def test_set_linear_discount(self, pt_oracle, llamarisk, owner):
        """Test that LlamaRisk can update linear discount parameters"""
        # First, advance time to ensure we can update
        boa.env.evm.patch.timestamp += 86401  # Move past the initial interval
        
        with boa.env.prank(llamarisk):
            pt_oracle.set_linear_discount(200, 100)
            assert pt_oracle.slope() == 200
            assert pt_oracle.intercept() == 100
            assert pt_oracle.last_discount_update() > 0
        
        # Non-LlamaRisk should fail
        with pytest.raises(Exception) as e:
            with boa.env.prank(owner):
                pt_oracle.set_linear_discount(300, 150)
        assert "dev: caller is not LlamaRisk" in str(e.value)
    
    def test_set_linear_discount_limits(self, pt_oracle, llamarisk):
        """Test that linear discount parameters can be set (no limits in current implementation)"""
        # Advance time to allow update
        boa.env.evm.patch.timestamp += 86401
        
        # Test that high values can be set (no limits in current implementation)
        with boa.env.prank(llamarisk):
            pt_oracle.set_linear_discount(2000, 1000)
            assert pt_oracle.slope() == 2000
            assert pt_oracle.intercept() == 1000
    
    def test_set_linear_discount_rate_limiting(self, pt_oracle, llamarisk):
        """Test that set_linear_discount is rate limited"""
        # First update should work after initial interval
        boa.env.evm.patch.timestamp += 86401
        with boa.env.prank(llamarisk):
            pt_oracle.set_linear_discount(200, 100)
            first_update = pt_oracle.last_discount_update()
        
        # Immediate second update should fail
        with pytest.raises(Exception) as e:
            with boa.env.prank(llamarisk):
                pt_oracle.set_linear_discount(300, 150)
        assert "dev: update interval not elapsed" in str(e.value)
        
        # Advance time by less than max_update_interval
        boa.env.evm.patch.timestamp += 43200  # 12 hours
        
        # Should still fail
        with pytest.raises(Exception) as e:
            with boa.env.prank(llamarisk):
                pt_oracle.set_linear_discount(300, 150)
        assert "dev: update interval not elapsed" in str(e.value)
        
        # Advance time to complete the interval
        boa.env.evm.patch.timestamp += 43201  # Past 24 hours total
        
        # Now it should work
        with boa.env.prank(llamarisk):
            pt_oracle.set_linear_discount(300, 150)
            assert pt_oracle.slope() == 300
            assert pt_oracle.intercept() == 150
            assert pt_oracle.last_discount_update() > first_update
    
    def test_set_limits(self, pt_oracle, curve_dao, owner):
        """Test that Curve DAO can update max update interval"""
        with boa.env.prank(curve_dao):
            pt_oracle.set_limits(172800)  # 48 hours
            assert pt_oracle.max_update_interval() == 172800
        
        # Non-Curve DAO should fail
        with pytest.raises(Exception) as e:
            with boa.env.prank(owner):
                pt_oracle.set_limits(86400)
        assert "dev: caller is not Curve DAO" in str(e.value)
    
    def test_set_limits_validation(self, pt_oracle, curve_dao):
        """Test that set_limits validates input"""
        # Test invalid update interval
        with pytest.raises(Exception) as e:
            with boa.env.prank(curve_dao):
                pt_oracle.set_limits(0)  # Invalid update interval
        assert "dev: invalid update interval" in str(e.value)
    
    def test_full_discount_returns_zero(self, owner):
        """Test that full discount (>=100%) returns 0 price"""
        from contracts import PtOracle
        
        # Create PT with long expiry
        mock_pt_code = """
# pragma version 0.4.3

expiry: public(uint256)

@deploy
def __init__(_expiry: uint256):
    self.expiry = _expiry
"""
        with boa.env.prank(owner):
            # Set expiry to 1000 days from now
            far_future_expiry = boa.env.evm.patch.timestamp + (1000 * 24 * 60 * 60)
            pt = boa.loads(mock_pt_code, far_future_expiry)
            
            # Create mock oracle
            mock_oracle_code = """
# pragma version 0.4.3

@view
@external
def price() -> uint256:
    return 10**18
"""
            mock_oracle = boa.loads(mock_oracle_code)
            
            # Deploy with high slope that will cause >100% discount
            pt_oracle = PtOracle.deploy(
                pt.address,
                mock_oracle.address,
                100000,  # High slope - with 1000 days, this exceeds 100%
                50000000,  # 50% intercept
                86400,
                boa.env.generate_address(),
                boa.env.generate_address()
            )
            
            assert pt_oracle.price() == 0
            assert pt_oracle.price_w() == 0
    
    def test_underlying_price_changes(self, pt_oracle, mock_oracle):
        """Test that PT price changes when underlying oracle price changes"""
        # Get initial price
        initial_price = pt_oracle.price()
        
        # Double the underlying price
        with boa.env.prank(boa.env.generate_address()):
            mock_oracle.set_price(2 * 10**18)
        
        # PT price should also increase (but still be discounted)
        new_price = pt_oracle.price()
        assert new_price > initial_price
        assert new_price < mock_oracle.price()  # Still discounted