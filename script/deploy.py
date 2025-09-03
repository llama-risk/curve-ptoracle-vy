from contracts import PtOracle
from moccasin.boa_tools import VyperContract

def deploy() -> VyperContract:
    # Example deployment parameters - these would need to be configured for actual deployment
    pt_address = "0x0000000000000000000000000000000000000001"  # Replace with actual PT token
    curve_pool = "0x0000000000000000000000000000000000000002"  # Replace with actual pool
    underlying_oracle = "0x0000000000000000000000000000000000000003"  # Replace with actual oracle
    slope_bps = 100  # 1% slope
    intercept_bps = 50  # 0.5% intercept
    max_update_interval = 86400  # 24 hours
    max_allowed_slope_bps = 1000  # 10% max slope
    max_allowed_intercept_bps = 500  # 5% max intercept
    llamarisk = "0x0000000000000000000000000000000000000004"  # Replace with actual LlamaRisk
    curve_dao = "0x0000000000000000000000000000000000000005"  # Replace with actual Curve DAO
    
    pt_oracle: VyperContract = PtOracle.deploy(
        pt_address,
        curve_pool,
        underlying_oracle,
        slope_bps,
        intercept_bps,
        max_update_interval,
        max_allowed_slope_bps,
        max_allowed_intercept_bps,
        llamarisk,
        curve_dao
    )
    
    print(f"PtOracle deployed at: {pt_oracle.address}")
    print(f"Current price: {pt_oracle.price()}")
    
    return pt_oracle

def moccasin_main() -> VyperContract:
    return deploy()
