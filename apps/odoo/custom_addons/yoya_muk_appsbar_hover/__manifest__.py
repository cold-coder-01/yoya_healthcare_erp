{
    "name": "YOYA MuK AppsBar Hover",
    "summary": "Hover expansion and independent scrolling for the MuK backend apps sidebar",
    "version": "18.0.1.0.0",
    "category": "Themes/Backend",
    "author": "YOYA Healthcare ERP",
    "license": "LGPL-3",
    "depends": ["muk_web_appsbar"],
    "assets": {
        "web.assets_backend": [
            (
                "after",
                "muk_web_appsbar/static/src/webclient/appsbar/appsbar.xml",
                "yoya_muk_appsbar_hover/static/src/webclient/appsbar_hover.xml",
            ),
            (
                "after",
                "muk_web_appsbar/static/src/webclient/appsbar/appsbar.scss",
                "yoya_muk_appsbar_hover/static/src/scss/appsbar_hover.scss",
            ),
        ],
    },
    "application": False,
    "installable": True,
}