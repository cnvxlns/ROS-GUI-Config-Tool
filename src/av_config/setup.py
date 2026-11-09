from setuptools import find_packages, setup

package_name = "av_config"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    # FastAPI 등은 venv 로 설치한다(§7.1). colcon 이 pip 로 끌어오지 않도록 비워 둔다.
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="han-yu",
    maintainer_email="vialaboratory.pnu@gmail.com",
    description="Web Configure App 백엔드",
    license="Proprietary",
    entry_points={
        "console_scripts": [
            "config_app = av_config.main:main",
        ],
    },
)
