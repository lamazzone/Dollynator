import json
import os
import re
import smtplib
import subprocess
import sys
import time
from argparse import ArgumentParser

import cloudomate
from cloudomate.cmdline import providers as cloudomate_providers
from cloudomate.util.config import UserOptions
from cloudomate.wallet import ElectrumWalletHandler
from cloudomate.wallet import Wallet
from plebnet import cloudomatecontroller
from plebnet.agent import marketapi
from plebnet.agent.dna import DNA
from plebnet.cloudomatecontroller import options
from plebnet.config import PlebNetConfig

TRIBLER_HOME = os.path.expanduser("~/tribler")
PLEBNET_CONFIG = os.path.expanduser("~/.plebnet.cfg")
TIME_IN_DAY = 60.0 * 60.0 * 24.0
MAX_DAYS = 5


def execute(cmd=sys.argv[1:]):
    parser = ArgumentParser(description="Plebnet")

    subparsers = parser.add_subparsers(dest="command")
    add_parser_check(subparsers)
    add_parser_setup(subparsers)

    args = parser.parse_args(cmd)
    args.func(args)


def add_parser_check(subparsers):
    parser_list = subparsers.add_parser("check", help="Check plebnet")
    parser_list.set_defaults(func=check)


def add_parser_setup(subparsers):
    parser_list = subparsers.add_parser("setup", help="Setup plebnet")
    parser_list.set_defaults(func=setup)


def setup(args):
    print("Setting up PlebNet")
    cloudomatecontroller.generate_config()
    config = PlebNetConfig()
    config.set('expiration_date', time.time() + 30 * TIME_IN_DAY)
    config.save()

    dna = DNA()
    dna.read_dictionary()
    dna.write_dictionary()
    # twitter.tweet_arrival(cp.get('firstname') + ' ' + cp.get('lastname'))


def check(args):
    """
    Check whether conditions for buying new server are met and proceed if so
    :param args: 
    :return: 
    """
    print("Checking")
    config = PlebNetConfig()

    dna = DNA()
    dna.read_dictionary()

    if not tribler_running():
        print("Tribler not running")
        start_tribler()

    if config.time_since_offer() > TIME_IN_DAY:
        print("Updating daily offer")
        chosen_est_price = update_choice(config, dna)
        place_offer(chosen_est_price, config)

    if plebnet_trial_mc_balance() and not config.get('test_offer'):
        print("Placing offer on Tribler market")
        chosen_est_price = get_price(config)
        place_offer(chosen_est_price, config)
        config.set('test_offer', True)

    if marketapi.get_btc_balance() >= get_cheapest_provider(config)[2]:
        print("Purchase server")
        success, provider = purchase_choices(config)
        if success:
            own_provider = get_own_provider(dna)
            evolve(own_provider, dna, success)
        else:
            evolve(provider, dna, success)


    install_available_servers(config, dna)
    config.save()


def tribler_running():
    """
    Check if tribler is running.
    :return: True if twistd.pid exists in /root/tribler
    """
    return os.path.isfile(os.path.join(TRIBLER_HOME, 'twistd.pid'))


def start_tribler():
    """
    Start tribler
    :return: 
    """
    env = os.environ.copy()
    env['PYTHONPATH'] = TRIBLER_HOME
    return subprocess.call(['twistd', 'plebnet', '-p', '8085', '--exitnode'], cwd=TRIBLER_HOME, env=env)


def is_evolve_ready():
    """
    Determine whether the pleb is ready to evolve
    :return: 
    """
    return True


def plebnet_trial_mc_balance():
    """
    Determines if plebnet has mc it can sell, used for trail
    :return: True if multichain balance is more than 0
    """
    return marketapi.get_mc_balance() > 0


def evolve(provider, dna, success):
    if success:
        dna.positive_evolve(provider)
    else:
        dna.negative_evolve(provider)


def update_choice(config, dna):
    choices = []
    all_providers = dna.dictionary
    excluded_providers = config.get('excluded_providers')
    available_providers = list(set(all_providers.keys()) - set(excluded_providers))
    providers = {k: all_providers[k] for k in all_providers if k in available_providers}
    print("Providers: %s" % providers)
    if providers >= 1:
        (provider, option, btc_price) = pick_provider(providers)
        choices.append((provider, option, btc_price))
        print("First provider: %s" % provider)
        del providers[provider]

    if config.time_to_expiration() > MAX_DAYS * TIME_IN_DAY and len(providers) >= 1:
        # if more than 5 days left, pick another, to improve margins
        (provider, option, btc_price) = pick_provider(providers)
        choices.append((provider, option, btc_price))
        print("Second provider: %s" % provider)
    config.set('chosen_providers', choices)
    return sum(i[2] for i in choices)


def get_price(config):
    price = 0.0
    for k in config.get('chosen_providers'):
        price += k[2]
    return price


def get_own_provider(dna):
    return dna.dictionary['Self']


def pick_provider(providers):
    provider = DNA.choose_provider(providers)
    gateway = cloudomate_providers[provider].gateway
    option, price, currency = pick_option(provider)
    btc_price = gateway.estimate_price(
        cloudomate.wallet.get_price(price, currency)) + cloudomate.wallet.get_network_fee()
    return provider, option, btc_price


def pick_option(provider):
    """
    Pick most favorable option at a provider. For now pick most bandwidth per bitcoin
    :param provider: 
    :return: (option, price, currency)
    """
    vpsoptions = options(cloudomate_providers[provider])
    values = []
    for item in vpsoptions:
        bandwidth = item.bandwidth
        if isinstance(bandwidth, str):
            bandwidth = float(item.connection) * 30 * TIME_IN_DAY
        values.append((bandwidth / item.price, item.price, item.currency))
    (bandwidth, price, currency), option = max((v, i) for (i, v) in enumerate(values))
    return option, price, currency


def place_offer(chosen_est_price, config):
    """
    Sell all available MC for the chosen estimated price on the Tribler market.
    :param config: config
    :param chosen_est_price: Target amount of BTC to receive
    :return: success of offer placement
    """
    available_mc = marketapi.get_mc_balance()
    if available_mc == 0:
        print("No MC available")
        return False
    config.bump_offer_date()
    config.set('last_offer', {'BTC': chosen_est_price, 'MC': available_mc})
    return marketapi.put_ask(price=chosen_est_price, price_type='BTC', quantity=available_mc, quantity_type='MC')


def get_cheapest_provider(config):
    """
    Get the price of the cheapest target.
    :param config: config
    :return: price
    """
    providers = config.get('chosen_providers')
    cheapest_provider = providers[0]
    min_price = cheapest_provider[2]
    for provider in providers:
        if provider[2] < min_price:
            min_price = provider[2]
            cheapest_provider = provider

    return cheapest_provider


def purchase_choices(config):
    """
    Purchase the cheapest provider in chosen_providers. If buying is successful this provider is moved to bought. In any
    case the provider is removed from choices.
    :param config: config
    :return: success
    """
    (provider, vps_option, btc_price) = get_cheapest_provider(config)

    success = cloudomatecontroller.purchase(provider, vps_option, wallet=Wallet())
    if success:
        config.get('bought').append(provider)
    config.get('chosen_providers').remove(provider)
    config.get('excluded_providers').append(provider)
    return success, provider


def install_available_servers(config, dna):
    bought = config.get('bought')

    for provider in bought:
        ip = subprocess.check_output(['cloudomate', 'getip', provider])
        if is_valid_ip(ip):
            user_options = UserOptions()
            user_options.read_settings()
            rootpw = user_options.get('rootpw')
            cloudomatecontroller.setrootpw(cloudomate_providers[provider], rootpw)
            dna.create_child_dna(provider)
            install_server(ip, rootpw)
            mail_message = 'IP: %s\n' % ip
            mail_message += 'Root password: %s\n' % rootpw
            mail_dna = DNA()
            mail_dna.read_dictionary()
            mail_message += '\nDNA\n%s\n' % json.dumps(mail_dna.dictionary)
            send_mail(mail_message, user_options.get('firstname') + ' ' + user_options.get('lastname'))


def is_valid_ip(ip):
    return re.match('\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', ip)


def install_server(ip, rootpw):
    success = subprocess.call(['../scripts/create-child.sh', ip, rootpw])
    if success:
        print("Installation successful")
    else:
        print("Installation unsuccesful")
    return success


def send_mail(mail_message, name):
    sender = name + '@pleb.net'
    receivers = ['plebnet@heijligers.me']

    mail = """From: %s <%s>
To: Jaap <plebnet@heijligers.me>
Subject: Pleb arrival

""" % (name, sender)
    mail += mail_message

    try:
        print("Sending mail: %s" + mail)
        smtp = smtplib.SMTP('mail.heijligers.me')
        smtp.sendmail(sender, receivers, mail)
        print "Successfully sent email"
    except smtplib.SMTPException:
        print "Error: unable to send email"


if __name__ == '__main__':
    execute()