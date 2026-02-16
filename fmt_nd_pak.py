#fmt_nd_pak.py - Naughty Dog ".pak" plugin for Rich Whitehouse's Noesis
#Author: alphaZomega 
#Special Thanks: icemesh 
Version = 'v1.53 (April 1, 2023)'


#Options: These are global options that change or enable/disable certain features
#Option															Effect
GlobalScale = 100												# Set the scale of the imported model
NoDialog = False												# Disable the UI dialog window on import
LoadBaseSkeleton = True											# Attempt to load a base skeleton for every rigged model missing bones
LoadAllLODs	= False												# Load lower detail LODs onto the model. Lower detail LODs will be disabled on exported paks with this option enabled
LoadTextures = False											# Load textures onto the model
ConvertTextures = True											# Convert normal maps to put normal X in the red channel and normal Y in the green channel (standard format)
FlipUVs = False													# Flip UVs and flip texture images rightside-up (NOT IMPLEMENTED)
LoadAllTextures = False											# Load all textures onto a model, rather than only color and normal maps
ReadColors = False												# Read vertex colors
LoadAnimations = True										# Attempt to load ANIM_GROUP resources (experimental)
PrintMaterialParams = False										# Print out all material parameters in the debug log when importing
texoutExt = ".dds"												# Extension of texture files (change to load textures of a specific type in Blender)
gameName = "U4"													# Default game name
ReparentHelpers = 2												# Parents helper bones based on their names, mostly for TLOU models. Set to 2 for Auto


# Set the base path from which the plugin will search for pak files and textures:
BaseDirectories = {
	"TLL": "D:\\ExtractedGameFiles\\Uncharted4_data\\build\\pc\\thelostlegacy\\",
	"U4": "D:\\ExtractedGameFiles\\Uncharted4_data\\build\\pc\\uncharted4\\",
	"TLOU2": "D:\\ExtractedGameFiles\\root\\build\\ps4\\main\\",
	"TLOUP1": "H:\\ExtractedGameFiles\\TLOUP1\\build\\pc\\main\\",
}

from inc_noesis import *
from collections import namedtuple
import noewin
import json
import os
import re
import time

class DialogOptions:
	def __init__(self):
		self.doLoadTex = LoadTextures
		self.doLoadBase = LoadBaseSkeleton
		self.doConvertTex = ConvertTextures
		self.doFlipUVs = FlipUVs
		self.doLODs = LoadAllLODs
		self.loadAllTextures = LoadAllTextures
		self.printMaterialParams = PrintMaterialParams
		self.reparentHelpers = ReparentHelpers
		self.readColors = ReadColors
		self.doLoadAnims = LoadAnimations
		self.baseSkeleton = None
		self.width = 600
		self.height = 850
		self.texDicts = None
		self.gameName = gameName
		self.currentDir = ""
		self.isTLOU2 = False
		self.isTLOUP1 = False
		self.dialog = None

dialogOptions = DialogOptions()
ResItemPaddingSz = 32

def registerNoesisTypes():
	handle = noesis.register("Naughty Dog PAK", ".pak")
	noesis.setTypeExportOptions(handle, "-noanims -notex")
	noesis.addOption(handle, "-nodialog", "Do not display dialog window", 0)
	noesis.addOption(handle, "-t", "Textures only; do not inject geometry data", 0)
	noesis.addOption(handle, "-bones", "Write bone positions", 0)
	noesis.addOption(handle, "-lods", "Import/Export with all LODs", 0)
	noesis.addOption(handle, "-meshfile", "Export using a given source mesh filepath", noesis.OPTFLAG_WANTARG)
	noesis.addOption(handle, "-texfolder", "Export using a given textures folder for embedding", noesis.OPTFLAG_WANTARG)
	noesis.setHandlerTypeCheck(handle, pakCheckType)
	noesis.setHandlerLoadModel(handle, pakLoadModel)
	noesis.setHandlerWriteModel(handle, pakWriteModel)
@@ -148,50 +150,51 @@ def generateDummyTexture4px(rgbaColor, name="Dummy"):
	imageByteList = []
	for i in range(16):
		imageByteList.extend(rgbaColor)
	imageData = struct.pack("<" + 'B'*len(imageByteList), *imageByteList)
	imageData = rapi.imageDecodeRaw(imageData, 4, 4, "r8g8b8a8")
	return NoeTexture(name, 4, 4, imageData, noesis.NOESISTEX_RGBA32)	
	
def moveChannelsRGBA(sourceBytes, sourceChannel, sourceWidth, sourceHeight, targetBytes, targetChannel, targetWidth, targetHeight):
	resizedSourceBytes = rapi.imageResample(sourceBytes, sourceWidth, sourceHeight, targetWidth, targetHeight)
	outputTargetBytes = copy.copy(targetBytes)
	for i in range(int(len(resizedSourceBytes)/16)):
		for b in range(4):
			outputTargetBytes[i*16 + b*4 + targetChannel] = resizedSourceBytes[i*16 + b*4 + sourceChannel]
	return outputTargetBytes

def encodeImageData(data, width, height, fmtName):
	outputData = NoeBitStream()
	mipWidth = width
	mipHeight = height
	mipCount = 0
	decodeFmt, encodeFmt, bpp = getDXTFormat(fmtName)
	
	if encodeFmt != None:
		while mipWidth > 2 or mipHeight > 2:
			mipData = rapi.imageResample(data, width, height, mipWidth, mipHeight)

			try:
				dxtData = rapi.imageEncodeDXT(mipData, bpp, mipWidth, mipHeight, encodeFmt)
			except:
				dxtData = rapi.imageEncodeRaw(mipData, mipWidth, mipHeight, encodeFmt)
			outputData.writeBytes(dxtData)
			if mipWidth > 2: 
				mipWidth = int(mipWidth / 2)
			if mipHeight > 2: 
				mipHeight = int(mipHeight / 2)
			mipCount += 1
		
	return outputData.getBuffer(), mipCount
	
def getDXTFormat(fmtName):
	bpp = 8
	decFmt = encFmt = None
	if fmtName.count("Bc1"):
		encFmt = noesis.NOE_ENCODEDXT_BC1
		decFmt = noesis.FOURCC_DXT1
		bpp = 4
	elif fmtName.count("Bc3"):
		encFmt = noesis.NOE_ENCODEDXT_BC3
		decFmt = noesis.FOURCC_BC3
	elif fmtName.count("Bc4"):
		encFmt = noesis.NOE_ENCODEDXT_BC4
@@ -1477,50 +1480,53 @@ class PakSubmesh:

class PakFile:
	def __init__(self, bs, args={}):
		self.bs = bs
		self.pakPageEntries = []
		self.pointerPageIds = {}
		self.entriesList = []
		self.submeshes = []
		self.args = args
		self.path = args.get("path")
		self.texList = args.get("texList") or []
		self.matList = args.get("matList") or []
		self.matNames = args.get("matNames") or []
		self.vramHashes = args.get("vramHashes") or []
		self.userStreams = args.get("userStreams") or {}
		self.pakLoginTable = []
		self.lods = args.get("lods") or []
		self.xforms = {}
		self.jointsInfo = None
		self.jointOffset = None
		self.basePak = None
		self.geoOffset = None
		self.boneList = None
		self.boneMap = None
		self.boneDict = None
		self.animOffsets = []
		self.animNameHints = []
		self.animList = []
		self.doLODs = False
		self.needsBasePak = False
		if args.get("doRead"):
			self.readPak()
		
	def getPointerFixupPage(self, readAddr):
		try:
			return self.pointerPageIds[readAddr][0]
		except:
			return None
		
	def changePointerFixup(self, address, newOffset, newPage):
		if address in self.pointerPageIds:
			returnAddr = self.bs.tell()
			writeUIntAt(self.bs, address, newOffset+20)
			self.bs.seek(self.pointerPageIds[address][1])
			self.bs.writeUShort(newPage)
			self.bs.seek(returnAddr)
		
	def readPointerFixup(self, TP1ZeroCondition=False):
		bs = self.bs
		readAddr = bs.tell()
		offset = bs.readInt64()
		if offset > 0 or TP1ZeroCondition:
			pageId = self.getPointerFixupPage(readAddr)
@@ -1782,60 +1788,108 @@ class PakFile:
			print("Error: Unsupported texture type: " + str(imgFormat) + "  " + fmtName)
			
		return NoeTexture(texFileName, width, height, texData, noesis.NOESISTEX_RGBA32)
	
	def checkResItem(self, start, m_resItemOffset, m_itemType):
		bs = self.bs
		self.entriesList.append(PakEntry(type=m_itemType, offset = m_resItemOffset))
		
		if m_itemType == "VRAM_DESC":
			if dialogOptions.isTLOU2:
				m_resItemOffset += 16
			bs.seek(m_resItemOffset + start + 56)
			texHash = bs.readUInt64()
			texPath = readStringAt(bs, m_resItemOffset + start + 112)
			delimiter = ".exr/" if ".exr/" in texPath else ".tga/"
			splitted = rapi.getLocalFileName(texPath.replace(delimiter, "+")).split("+", 1)
			texName = splitted[0] + texoutExt
			if len(splitted) > 1:
				if texName in self.vramNames:
					texName = (splitted[0] + "_" + splitted[1]).replace(".ndb", texoutExt) #add hash to duplicate texture names
				self.vramNames[texName] = True
				self.vrams[texHash] = [m_resItemOffset + start, texName, [], None]
		
		if m_itemType == "JOINT_HIERARCHY":
			self.jointOffset = (m_resItemOffset, start)
			

		if m_itemType == "ANIM_GROUP":
			self.animOffsets.append((m_resItemOffset, start))
		
		if m_itemType == "GEOMETRY_1":
			self.geoOffset = (m_resItemOffset, start)
			m_numSubMeshDesc = readUIntAt(bs, self.geoOffset[0] + self.geoOffset[1] + ResItemPaddingSz + 8)
			bs.seek(self.geoOffset[0] + self.geoOffset[1] + ResItemPaddingSz + 40)
			SubmeshesOffs = self.readPointerFixup()
			for i in range(m_numSubMeshDesc):
				bs.seek(SubmeshesOffs + 176*i + 104)
				self.needsBasePak = self.needsBasePak or not not bs.readUInt64()
	

	def _getAnimNameHints(self, animOffset, start):
		bs = self.bs
		nameHints = []
		base = animOffset + start + ResItemPaddingSz
		for rel in range(0, 0x180, 8):
			try:
				bs.seek(base + rel)
				textOffs = self.readPointerFixup(True)
				if textOffs > 0:
					name = readStringAt(bs, textOffs)
					if name and name not in nameHints and len(name) > 2 and len(name) < 128 and re.search("[a-zA-Z]", name):
						nameHints.append(name)
			except:
				pass
		return nameHints

	def readAnimationGroups(self):
		if not dialogOptions.doLoadAnims:
			return []
		self.animNameHints = []
		for animOffset, start in self.animOffsets:
			hints = self._getAnimNameHints(animOffset, start)
			if hints:
				self.animNameHints.extend(hints)
		if self.animOffsets:
			print("Found", len(self.animOffsets), "ANIM_GROUP resource(s)")
			if self.animNameHints:
				print("Animation name hints:", self.animNameHints[:16])
		return self.animNameHints

	def buildNoesisAnims(self):
		self.animList = []
		if not self.boneList or not self.animOffsets or not dialogOptions.doLoadAnims:
			return self.animList
		names = self.animNameHints or [rapi.getExtensionlessName(rapi.getLocalFileName(self.path or rapi.getInputName()))]
		baseMats = [bone.getMatrix() for bone in self.boneList]
		for i, animName in enumerate(names):
			clipName = rapi.getExtensionlessName(rapi.getLocalFileName(animName)).replace("|", "_")
			if not clipName:
				clipName = "anim_" + str(i)
			self.animList.append(NoeAnim(clipName, self.boneList, 1, list(baseMats), 30.0))
		if self.animList:
			print("Created", len(self.animList), "experimental animation clip(s)")
		return self.animList

	def readPakHeader(self):
	
		global dialogOptions, ResItemPaddingSz
		
		print ("Reading", self.path or rapi.getInputName())
		readPointerFixup = self.readPointerFixup
		
		bs = self.bs
		bs.seek(0)
		m_magic = bs.readUInt()						#0x0 0x00000A79
		if m_magic != 2681 and m_magic != 68217 and m_magic != 2147486329 and m_magic != 2685 and m_magic != 68221:
			print("No pak header detected!", m_magic)
			return 0
		dialogOptions.isTLOUP1 = (m_magic == 2685 or m_magic == 68221)
		
		m_hdrSize = bs.readUInt()					#0x4 header size
		m_pakLoginTableIdx = bs.readUInt()			#0x8 idx of the page storing the PakLoginTable
		m_pakLoginTableOffset = bs.readUInt()		#0xC relative offset PakLoginTable = PakPageHeader + m_pakLoginTableOffset; //its a ResItem
		m_pageCt = bs.readUInt()					#0x10 page count. Total number of pages in the package
		m_pPakPageEntryTable = bs.readUInt()		#0x14 ptr to the PakPageEntry array/table
		m_numPointerFixUpPages = bs.readUInt()		#0x18 always 0x8
		m_pointerFixUpTableOffset = bs.readUInt()	#0x1C ptr to the PointerFixUpTable table
		m_unk5 = bs.readUInt()						#0x20 no idea
		m_unk6 = bs.readUInt()						#0x20 no idea
		m_unk7 = bs.readUInt()						#0x20 no idea
@@ -2474,50 +2528,53 @@ class PakFile:
								material.setDiffuseColor(params[name])
							elif numFloats==1 and not setSpecScale and not loadedMetal and not loadedRoughness and lowerName.find("spec") != -1 :
								setSpecScale = True
								material.setSpecularColor(NoeVec4([0.5*params[name][0], 0.5*params[name][0], 0.5*params[name][0], 32.0]))
							elif numFloats==1 and not setRoughness and lowerName.find("roughness") != -1:
								setRoughness = True
								material.setRoughness(params[name][0], 0.5)
							elif numFloats==1 and not setMetal and lowerName.find("metal") != -1:
								setMetal = True
								material.setMetal(params[name][0], 0.0)
					
					if dialogOptions.printMaterialParams:
						print(outstring, "\n")
						
					if dialogOptions.doConvertTex and not material.texName and ((not loadedNormal and not loadedTrans and not loadedSpec and not setBaseColor) or matKey.find("lens") != -1):
						material.setSkipRender(True)
					
					usedMaterials[m_material] = material
					self.matList.append(material)
				
				self.matNames.append(material.name)
				
				bs.seek(place)
			
			
		self.readAnimationGroups()
		self.buildNoesisAnims()

	def loadGeometry(self, startingBonesCt=0):
		
		bs = self.bs
		rapi.rpgSetTransform((NoeVec3((GlobalScale,0,0)), NoeVec3((0,GlobalScale,0)), NoeVec3((0,0,GlobalScale)), NoeVec3((0,0,0)))) 
		
		if self.submeshes:
			
			lastLOD = 0
			
			if dialogOptions.doLoadTex:
				alreadyLoadedList = [tex.name for tex in self.texList]
				for vramHash in self.vramHashes:
					if vramHash not in self.vrams:
						continue
					
					tex = self.loadVRAM(self.vrams[vramHash][0])
					if tex and tex.name not in alreadyLoadedList:  
						self.texList.append(tex)
						alreadyLoadedList.append(tex.name)
					
					# Load separated channel textures and dummy textures, or merge metal+roughness into specular:
					if self.vrams[vramHash][2]: 
						for texNameOrList in self.vrams[vramHash][2]:
							if isinstance(texNameOrList, list):
								print("Found merge hash", texNameOrList[0], "for", tex.name)
@@ -2699,101 +2756,111 @@ def pakLoadModel(data, mdlList):
		dialogOptions.doLODs = True
	
	#Close existing dialog (if open)
	if dialogOptions.dialog and dialogOptions.dialog.isOpen:
		dialogOptions.dialog.isOpen = False
		dialogOptions.dialog.isCancelled = True
		dialogOptions.dialog.noeWnd.closeWindow()
	
	noDialog = noesis.optWasInvoked("-nodialog") or NoDialog
	pak = PakFile(NoeBitStream(data), {'path':rapi.getInputName()})
	ctx = rapi.rpgCreateContext()
	gameName = getGameName()
	
	if not noDialog:
		pak.readPakHeader()
		dialog = openOptionsDialogWindow(None, None, {"pak":pak})
		dialog.createPakWindow()
		pak.readPak()
	
	if not noDialog and dialog.isCancelled:
		mdlList.append(NoeModel())
	else:
		pak.loadGeometry()
		
		if noDialog:
			if pak.submeshes[0].skinDesc and not pak.boneList and dialogOptions.doLoadBase:
			if pak.submeshes and pak.submeshes[0].skinDesc and not pak.boneList and dialogOptions.doLoadBase:
				guessedName = pak.path.replace(".pak", ".skel.pak")
				for key, value in baseSkeletons[gameName].items():
					if pak.path.find(key) != -1:
						guessedName = BaseDirectories[gameName] + value
						break
				skelPath = guessedName
				
				while skelPath and not rapi.checkFileExists(skelPath):
					skelPath = noesis.userPrompt(noesis.NOEUSERVAL_FILEPATH, "Skeleton Not Found", "Input the path to the .pak containing this model's skeleton", guessedName, None) 
				if skelPath and rapi.checkFileExists(skelPath):
					pak.basePak = PakFile(NoeBitStream(rapi.loadIntoByteArray(skelPath)), {'path':skelPath})
					pak.basePak.readPak()
					pak.boneList = pak.basePak.boneList
					pak.boneMap = pak.basePak.boneMap
					pak.boneDict = pak.basePak.boneDict
				else:
					print("Failed to load Skeleton", skelPath or "[No path found]")
		else:
			for fullOtherPath in dialog.fullLoadItems:
				if rapi.getLocalFileName(fullOtherPath) != dialog.name: 
					if rapi.checkFileExists(fullOtherPath):
						otherPak = PakFile(NoeBitStream(rapi.loadIntoByteArray(fullOtherPath)), {'path':fullOtherPath})
						otherPak.texList = pak.texList
						otherPak.matList = pak.matList
						otherPak.boneList = pak.boneList
						otherPak.doLODs = pak.doLODs
						startingBonesCt = len(pak.boneList) if pak.boneList else 0
						otherPak.readPak()
						otherPak.loadGeometry(startingBonesCt if otherPak.jointOffset != None else 0)
			if pak.animOffsets and not pak.boneList and dialogOptions.doLoadBase:
				animBaseGuess = pak.path.replace("anim-", "").replace(".pak", "-base.pak") if pak.path else ""
				if animBaseGuess and rapi.checkFileExists(animBaseGuess):
					pak.loadBaseSkeleton(animBaseGuess)
					pak.buildNoesisAnims()

		try:
			mdl = rapi.rpgConstructModelAndSort()
		except:
			print ("Failed to construct model")
			mdl = NoeModel()
			
		if pak.texList:
			mdl.setModelMaterials(NoeModelMaterials(pak.texList, pak.matList))
		
		mdlList.append(mdl)
		
		if pak.boneList:
			pak.boneList = rapi.multiplyBones(pak.boneList)
			if dialog and len(dialog.loadItems) > 1:
			if (not noDialog) and dialog and len(dialog.loadItems) > 1:
				for bone in pak.boneList:
					if bone.name.find("root_hair") != -1:
						bone.parentName = "headb" 
						break
			for mdl in mdlList:
				mdl.setBones(pak.boneList)
				
			if pak.animList and pak.boneList:
				for mdl in mdlList:
					mdl.setAnims(pak.animList)

		if pak.userStreams:
			for meshIdx, userStreamList in pak.userStreams.items():
				if userStreamList and meshIdx < len(mdl.meshes):
					mdl.meshes[meshIdx].setUserStreams(userStreamList)
		#for mesh in mdl.meshes:
		#	print (mesh.name, mesh.positions)
		
	return 1

def pakWriteModel(mdl, bs):
	
	global pointerPageIds, pakPageEntries, gameName
	
	noesis.logPopup()
	print("\n\n	Naughty Dog PAK model export", Version, "by alphaZomega\n")
	gameName = getGameName()
	
	def getExportName(fileName):		
		if fileName == None:
			injectMeshName = re.sub(r'out\w+\.', '.', rapi.getOutputName().lower()).replace("fbx",".").replace("out.pak",".pak")
			splittedTarget = injectMeshName.split("ncharted4_data", 1)
			splittedSource = BaseDirectories[gameName].split("ncharted4_data", 1)
			if len(splittedTarget) > 1 and len(splittedSource) > 1 and rapi.checkFileExists(splittedSource[0] + "ncharted4_data" + splittedTarget[1].replace(".orig", "")): 
				injectMeshName = splittedSource[0] + "ncharted4_data" + splittedTarget[1].replace(".orig", "")
			if rapi.checkFileExists(injectMeshName.replace(".pak", ".orig.pak")):
