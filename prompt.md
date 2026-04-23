I need to build desktop software for Windows but I only have MacOS (can run VM to test stuff)

This software to upload a bunch of folders and files from an inserted USB stick to that machine, and perform bulk jobs image processing. Since we're processing large files, we need resumable progress in case things get interrupted.

The idea goes as follow:
1. Upload from a USB drive to: N:\RPMS$\<year of data>
Involves creating folder N:\RPMS$\<year>
2. Create thumbnails into older N:\RPMS$\TN\<year>  NOTE: only the folders within N:\RPMS$\2023\2023 Highway Images
Using an opensource tool called “GraphicsMagick”
Located in folder  N:\RPMS$\TN\GraphicsMagick-1.3.23-Q8\
Thumbnail size should be: " -resize 320x254 -quality 75 * “
3. Ensure a “LINK” is created or already exists in H:\PLGwww\hr that points to the High Res folders at N:\RPMS$\<year>
Command prompt link creation example: mklink /J H:\PLGwww\hr\2023      "N:\RPMS$\2023\2023 Highway Images"
4. Ensure a “LINK” is created or already exists in H:\PLGwww\TN that points to the High Res folders at N:\RPMS$\TN\<year>

This is a thing that is done yearly. Contractor comes to us with the USB stick containing the data we need, and we need to do that above every year.


Also, take these into considerations for your plan:

Upon USB stick inserted, user has to copy into RPMS$/<year>/. After copying is finished, it looks like this inside:

2023 Highway Images			2023_sitec_extras_image_gis_dataset.shp	hwy_dist.dbf
2023 SideRoad Images			2023_sitec_extras_image_gis_dataset.shx	hwy_irirut.dbf
2023 SiteC Images			bc_2023_align.dbf			hwy_sitec_dist.dbf
2023_image_gis_dataset.csv		bc_2023_align.prj			hwy_sitec_irirut.dbf
2023_image_gis_dataset.dbf		bc_2023_align.shp			IRI_Rut
2023_image_gis_dataset.prj		bc_2023_align.shx			SiteC_Distress
2023_image_gis_dataset.shp		bc_2023_sitec_extras_align.dbf		SiteC_IRI_Rut
2023_image_gis_dataset.shx		bc_2023_sitec_extras_align.prj		sr_dist.dbf
2023_sitec_extras_image_gis_dataset.csv	bc_2023_sitec_extras_align.shp		sr_irirut.dbf
2023_sitec_extras_image_gis_dataset.dbf	bc_2023_sitec_extras_align.shx		sr_sitec_dist.dbf
2023_sitec_extras_image_gis_dataset.prj	Distress				sr_sitec_irirut.dbf


But for thumbnail generation, we're only interested in "<year> Highway Images"

Folder RPMS$/<year>/  has a folder called "<year> Highway Images". This is the folder we're looking to work with. Please account for human error in the case that the folder name might have typo or something; might even want to consider letting user choose

"<year> Highway Images" folder looks like this:
H1_E_00_km0000		H16_E_00_km0496		H1W_W_13_BH_km0000	H31A_E_00_km0000	H52_E_00_km0136		H97_N_00_km0976
H1_E_00_km0073		H16_E_00_km0525		H1W_W_13_BH_km0006	H33_N_00_km0000		H52_E_00_km0194		H97_N_00_km1018
H1_E_00_km0171		H16_E_00_km0586		H2_N_00_km0000		H33_N_00_km0048		H5A_N_00_km0000		H97_N_00_km1099

The generated thumbnails must live in RPMS$/TN/<year>. The generated thumbnails must use the same name as the source images. The thumbnail size should be 320x254.
RPMS$/TN/ also has GraphicsMagick-1.3.23-Q8	tndir.bat and Imagemagick. Depending on what's fastest and most optimized, you have to decide whether to use that which already exists, or use an ImageMagick package from the tool (if exists and reputable), or use a different package entirely

ImageMagick has been used with the flags " -resize 320x254 -quality 75 * “. If you're using a different tool, we need to be close or exact to that parameter.

I will prepare sample folders later. I'm also provisioning a windows VM to do our tests on. Keep in mind most of the development will be done in MacOS Macbook Air 2022 M2, testing in Windows will only be done after every checkpoint. We are shipping this software for Windows. So it could be nice to simulate how it works on Windows, but in MacOS. The Windows version we're targeting is System Model: VMware Virtual Platform, OS version: Windows Server 2016 Standard. However, keep in mind the OS version is expecting an upgrade to 2022 this year. So we need robustness to survive the upgrade.

The problem is that I can't install Windows Server 2016 on my MacOS using the vmware fusion. Architecture is different. Will Windows Server 2022 standard be an option to install on vmware on this mac?

We are interested in using CustomTkinter using Python. Therefore .exe for Windows will have runtime bundled in.

To reiterate the user flow, the user needs to execute two processes: Copy the files from USB into the drive, and generate the thumbnails. These two processes must be able to run independently (for example, user can directly generate thumbnails based on a chosen folder).
Copying the files must be a 100% completion. If for whatever reason copying is interrupted, user must be able to resume copying them.
Generating thumbnail process should also be aware if it's attempting to replace existing files.
Progress and estimated time to completion must be shown for both. It'd also be nice to show status of disk, such as storage, upload speed, etc.

Please also propose what the UI should be like.

As this is a yearly operation that will continue for years, we prioritize robustness. We don't want anything like npm nightmare where package dependencies need update all the time.