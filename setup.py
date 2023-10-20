import setuptools

setuptools.setup(
    name='ezmsg-unicorn',
    packages=setuptools.find_namespace_packages(include=['ezmsg.*']),
    zip_safe=False
)
