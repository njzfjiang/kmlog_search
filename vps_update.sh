DATE=$(date +%Y%m%d_%H%M%S)
cd  ~/opt/kmlog-search
git add .
git commit -m "mother or WB update on $DATE"
git push origin master
