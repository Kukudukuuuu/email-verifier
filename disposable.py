"""Curated list of disposable / throwaway email domains.

The list is intentionally small and hand-picked; it is a *hint* (shown as a
warning), not a verdict. Plenty of missing domains will land here too.
"""

DISPOSABLE_DOMAINS = frozenset(
    d.strip().lower()
    for d in """
    0-mail.com 0clickemail.com 20minutemail.com 33mail.co.uk 33mail.net
    60minutemail.com 1secmail.com 1secmail.net 1secmail.org 1ownership.com
    10minutemail.co.uk 10minutemail.com 10minutemail.net 123-m.com
    anonbox.net anonymmail.net antispam.de antispam24.de antirelay.com
    bccto.me burnermail.io binkmail.com bobmail.info bombom5.com
    bugmenot.com buyusedlibrarybooks.org byom.de
    crazymailing.com crossmailjet.com
    deadaddress.com despam.it discard.cf discard.email discardmail.com
    disposeamail.com disposablemail.com dmail.org dodgemail.com dsfgdfsgf.com
    dropmail.me
    email-fake.com email-freedom.com emailfake.com emailondeck.com emailo.pro
    fakeinbox.com faxvin.com fake-mail.net filter-mx.com flashmail.win
    get-airmail.com getmails.eu getnada.com grr.la guerrillamail.biz
    guerrillamail.com guerrillamail.de guerrillamail.net guerrillamail.org
    guerrillamailblock.com
    harakirimail.com hurra1.de
    inbox-bear.com inboxbear.com inboxkitten.com incognitomail.com instant-mail.de
    jetable.org junk.bz junk1e.de junkmail.com
    kurzepost.de
    linshiyouxiang.net listmail.net luxusmailer.org
    mail7.io mailcatch.com maildrop.cc mailnesia.com mailsac.com
    mailtemp.info mailtothis.com mailzilla.org mao-in.com
    megapay.ameridns.org meltmail.info mintemail.com miskatonic.org
    moakt.com moakt.ws mohmal.com moi-meme.fr mvrht.net my10minutemail.com
    mytemp.email mytrashmail.com
    nada.email nikudeals.com nomail.xl.cx notmail.me nowmymail.com
    one-time.email onetime.email
    phoenixPage.org petsier8549.com plumbingarc.com porsh.net
    qabq.com
    ram-interiorsltd.com rcpt.at rhyta.com
    safetymail.info saiowdd.space sharklasers.com simplyemail.com
    sendspamhere.com spam4.me spambog.com spambog.de spambox.us
    spamgourmet.com spam4.me spamfree24.org spamhole.com spaml.de
    superrito.com
    tapir.back.mail-temporaire.fr teleworm.us tempail.com tempemail.net
    tempinbox.com tempmail.plus tempmailaddress.com temp-mail.io temp-mail.org
    tempmailo.com temporarioemail.com.br tempr.email throwam.com
    throwawaymail.com tmail.ws trash-mail.at trash-mail.com trash2009.com
    trashmail.at trashmail.com trash-mail.de trashmail.de trashmail.me
    trashmail.net tsovin.com
    vomoto.com vanymail.ddns.net vipmail.icu vpsbol.com
    webmails.live waveformvaluesweekend.com what1s.com wpdome.com
    xojxe.com xa835.com xww.world yopmail.com yopmail.fr yopmail.net
    yoggm.com yzco.co.uk yy9.cc
    zainmax.net ziplink.com zoho.com.tr zxcv.com.ua
    """.split()
)


def is_disposable(domain: str) -> bool:
    return domain.lower() in DISPOSABLE_DOMAINS
