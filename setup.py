from setuptools import setup, find_packages


with open("README.md") as f:
    readme = f.read()


setup(
    name="jupyter_http_terminal",
    packages=find_packages(),
    version='0.1.3',
    author="Steven Ding",
    author_email="",
    classifiers=[
        "Intended Audience :: Developers",
        "Intended Audience :: System Administrators",
        "Intended Audience :: Science/Research",
        "License :: OSI Approved :: BSD License",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    description="Run a http-terminal on Jupyter",
    entry_points={
        'jupyter_serverproxy_servers': [
            'httpterminal = jupyter_http_terminal.server:setup_jupyter_server_proxy',
        ]
    },
    install_requires=['jupyter-server-proxy>=1.4.0'],
    include_package_data=True,
    keywords=["Interactive", "Desktop", "Jupyter"],
    license="BSD",
    long_description=readme,
    long_description_content_type="text/markdown",
    platforms="Linux",
    project_urls={
        "Source": "https://github.com/L1NNA/jupyter_http_terminal/",
        "Tracker": "https://github.com/L1NNA/jupyter_http_terminal/issues",
    },
    python_requires=">=3.6",
    url="https://L1NNA.com",
    zip_safe=False
)
