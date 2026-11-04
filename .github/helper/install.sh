#!/bin/bash
# Provisions a bench, installs ERPNext and Upande Agriculture onto a fresh test
# site, and leaves it ready for the test run. Branches are overridable via the
# FRAPPE_BRANCH / ERPNEXT_BRANCH / PAYMENTS_BRANCH environment variables.

set -e

cd ~ || exit

sudo apt update
sudo apt remove -y mysql-server mysql-client || true
sudo apt install -y libcups2-dev redis-server mariadb-client libmariadb-dev pkg-config

pip install frappe-bench

frappeuser=${FRAPPE_USER:-"frappe"}
frappebranch=${FRAPPE_BRANCH:-"version-16"}
erpnextbranch=${ERPNEXT_BRANCH:-"version-16"}
paymentsbranch=${PAYMENTS_BRANCH:-"version-16"}

git clone "https://github.com/${frappeuser}/frappe" --branch "${frappebranch}" --depth 1
bench init --skip-assets --frappe-path ~/frappe --python "$(which python)" frappe-bench

mkdir ~/frappe-bench/sites/test_site
cp -r "${GITHUB_WORKSPACE}/.github/helper/site_config.json" ~/frappe-bench/sites/test_site/

mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "SET GLOBAL character_set_server = 'utf8mb4'"
mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "SET GLOBAL collation_server = 'utf8mb4_unicode_ci'"

mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "CREATE USER 'test_frappe'@'localhost' IDENTIFIED BY 'test_frappe'"
mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "CREATE DATABASE test_frappe"
mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "GRANT ALL PRIVILEGES ON \`test_frappe\`.* TO 'test_frappe'@'localhost'"

mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "FLUSH PRIVILEGES"

install_wkhtmltopdf() {
    wget -O /tmp/wkhtmltox.tar.xz https://github.com/frappe/wkhtmltopdf/raw/master/wkhtmltox-0.12.3_linux-generic-amd64.tar.xz
    tar -xf /tmp/wkhtmltox.tar.xz -C /tmp
    sudo mv /tmp/wkhtmltox/bin/wkhtmltopdf /usr/local/bin/wkhtmltopdf
    sudo chmod o+x /usr/local/bin/wkhtmltopdf
}
install_wkhtmltopdf &

cd ~/frappe-bench || exit

sed -i 's/watch:/# watch:/g' Procfile
sed -i 's/schedule:/# schedule:/g' Procfile
sed -i 's/socketio:/# socketio:/g' Procfile
sed -i 's/redis_socketio:/# redis_socketio:/g' Procfile

bench get-app "https://github.com/${frappeuser}/payments" --branch "$paymentsbranch"
bench get-app "https://github.com/${frappeuser}/erpnext" --branch "$erpnextbranch" --resolve-deps

# upande_core carries the masters these apps Link to (Farm, Business Unit, Cut
# Stage) — frappe validates Link options while syncing doctypes and
# customizations, so the install aborts without them. upande_packhouse is a
# hard dependency of this app: its Stock Entry.custom_bucket_id (and Bucket Reuse Anomaly)
# Link at packhouse's Bucket QR Code, so packhouse must be installed
# first.
bench get-app "${UPANDE_CORE_REPO:-https://github.com/upandeltd/Upande-Core.git}" --branch "${UPANDE_CORE_BRANCH:-main}"
bench get-app "${UPANDE_PACKHOUSE_REPO:-https://github.com/mark-judah/upande-packhouse-frappe}" --branch "${UPANDE_PACKHOUSE_BRANCH:-develop}"

bench get-app upande_agriculture "${GITHUB_WORKSPACE}"
bench setup requirements --dev

bench start &>> ~/frappe-bench/bench_start.log &
CI=Yes bench build --app frappe &
bench --site test_site reinstall --yes

# Seed ERPNext's shared test fixtures (_Test Customer, _Test Company, ...)
# BEFORE the Upande apps land. This app's FrappeTestCase suites make the runner
# preload test records for every doctype, which walks Link fields into
# Warehouse and imports erpnext.tests.utils -> BootStrapTestData. Packhouse
# makes Customer.default_price_list mandatory, so those fixture inserts fail
# once it is installed. BootStrapTestData skips records that already exist, so
# seeding them here makes the later bootstrap a no-op.
bench --verbose --site test_site install-app erpnext
bench --site test_site execute erpnext.tests.utils.BootStrapTestData

# Order matters: core (masters) -> packhouse -> agriculture, because
# agriculture Links at packhouse's Bucket QR Code.
bench --verbose --site test_site install-app upande_core
bench --verbose --site test_site install-app upande_packhouse
bench --verbose --site test_site install-app upande_agriculture
