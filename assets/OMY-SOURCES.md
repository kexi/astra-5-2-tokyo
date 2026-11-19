# 大丸有の22棟と東京駅地下街

三菱地所設計の公開事例図（4ページ目）で対象物件の名称と位置を確認し、対応する主棟の外形と写真を国土交通省PLATEAU 2025年度千代田区データから抽出した。元のBIM LOD200・Revit・IFCの一般配布ファイルは確認できていない。本作品で地上22棟に使用した形状はPLATEAU LOD2.2である。[三菱地所設計の事例図](https://bim-design.com/uploads/Mitsubishi-Jisho-CaseStudy_4P_ja.pdf#page=4)・[公開建物タイル](https://api.plateauview.mlit.go.jp/datacatalog/3dtiles/13101-bldg-lod2-texture-2025/tileset.json)・[PLATEAU利用規約](https://www.mlit.go.jp/plateau/site-policy/)

各棟の建物ID・GML ID・元タイル・棟名・位置の照合に使った資料を対応表に収録した。敷地内の屋根に4 m間隔の垂線を当て、主棟の地物を特定した。星のや東京と丸ノ内ホテルは施設位置付近の複数点で照合した。三菱UFJ信託銀行本店ビルと日本工業倶楽部会館は一体の保存・再生事業で、PLATEAUの同じ地物が高層部と保存棟を含む。各建物のカスタムプロパティに元の名称も残した。[建物対応表](../tools/omy-buildings.json)・[設計者の物件ページ](https://www.mjd.co.jp/projects/29279/)・[丸ノ内ホテルのアクセス図](https://www.marunouchi-hotel.co.jp/about/access/)

棟の位置の照合には © OpenStreetMap contributors の建物外形・地点を使用した。位置の対応表はODbL 1.0で提供する。外形メッシュと写真はPLATEAUから加工した。22棟の60,709三角形のうち、原典に画像があるのは47,552面。初版ではこの写真を棟別のアトラスに移し、写真がない面には元の頂点色と材質を使用した。現行版では補修した外壁写真、写真欠損を補う推定仕上げ、上面に合わせたオルソ画像を棟専用の1枚に収録し、貼り付けた面の種類をBlenderに記録している。[全棟のテクスチャ制作と出典](https://github.com/kexi/astra-5-2-tokyo/blob/main/assets/BUILDING-TEXTURES.md)・[OpenStreetMapの出典](https://www.openstreetmap.org/copyright)・[ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/)

丸の内・大手町・常盤橋では、各棟を一周して外壁に周囲の建物や屋上が写り込んだ箇所を特定し、同じ棟で屋根や設備が重なっていない面から窓列と外装材の写真を採って補修した。対象の三角形数と採取元の壁面を棟別に記録している。[壁面ごとの補修範囲](OMY-FACADE-REPAIRS.md)

丸の内二丁目ビルには東京都の航空レーザ点群とオルソ画像を使って立体の屋上設備を加えた。地下街は国土交通省・JR東日本コンサルタンツ株式会社の2023年度公開LOD4.0を使い、国土交通省の屋内地図で位置を合わせた。床・壁の表面仕上げと目地は表示用に生成した。[二丁目ビルの測量・加工内容](MARUNOUCHI-SOURCES.md)・[地下街のデータ・利用条件と加工内容](OMY-UNDERGROUND-SOURCES.md)
