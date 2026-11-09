from glob import glob

from setuptools import find_packages, setup

package_name = "av_bringup"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="han-yu",
    maintainer_email="vialaboratory.pnu@gmail.com",
    description="서브시스템 launch 파일",
    license="Proprietary",
    entry_points={"console_scripts": []},
)
