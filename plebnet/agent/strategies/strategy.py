from abc import ABCMeta, abstractmethod

from plebnet.agent.config import PlebNetConfig
from plebnet.controllers import market_controller
from plebnet.controllers.cloudomate_controller import calculate_price, calculate_price_vpn
from plebnet.settings import plebnet_settings
from plebnet.utilities import logger
from plebnet.utilities.btc import btc_to_satoshi

log_name = "agent.strategies.strategy"
BTC_FLUCTUATION_MARGIN = 1.15


class Strategy():
    __metaclass__ = ABCMeta

    def __init__(self):
        self.config = PlebNetConfig()

    @abstractmethod
    def apply(self):
        """
        Performs the whole strategy step for one plebnet check iteration
        :return:
        """
        pass

    @abstractmethod
    def sell_reputation(self):
        """
        Sells or holds current reputation (MB) depending on the implementing strategy
        :return:
        """
        pass

    @abstractmethod
    def create_offer(self, amount_mb, timeout):
        """
        Creates a new order in the market, with parameters depending on the implementing strategy
        :return:
        """
        pass

    def get_available_mb(self):
        return market_controller.get_balance('MB')

    @staticmethod
    def get_replication_price(vps_provider, option, vpn_provider='azirevpn'):
        return (calculate_price(vps_provider, option) + calculate_price_vpn(vpn_provider)) * BTC_FLUCTUATION_MARGIN

    def update_offer(self, mb_amount, timeout=plebnet_settings.TIME_IN_HOUR):
        """
        Check if "timeout" has passed since the last offer made, if passed create a new offer.
        """
        if self.config.time_since_offer() > timeout:
            logger.log("Calculating new offer", log_name)
            self.config.save()
            return self.create_offer(mb_amount, timeout)

    def place_offer(self, mb_amount, chosen_est_price, timeout, config):
        """
        Sells the received MB amount for the chosen estimated price on the Tribler market.
        :param mb_amount: Amount of MB to sell
        :param config: config
        :param timeout: timeout of the offer to place
        :param chosen_est_price: Target amount of BTC to receive
        :return: success of offer placement
        """
        if chosen_est_price == 0 or mb_amount == 0:
            return False
        config.bump_offer_date()

        coin = 'TBTC' if plebnet_settings.get_instance().wallets_testnet() else 'BTC'

        config.set('last_offer', {coin: chosen_est_price, 'MB': mb_amount})

        if coin == 'TBTC':
            return market_controller.put_ask(first_asset_amount=mb_amount,
                                             first_asset_type='MB',
                                             second_asset_amount=btc_to_satoshi(chosen_est_price),
                                             second_asset_type=coin,
                                             timeout=timeout)
        return market_controller.put_bid(first_asset_amount=btc_to_satoshi(chosen_est_price),
                                         first_asset_type=coin,
                                         second_asset_amount=mb_amount,
                                         second_asset_type='MB',
                                         timeout=timeout)
