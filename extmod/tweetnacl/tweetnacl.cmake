# Create an INTERFACE library for our C module.
add_library(micropy_extmod_tweetnacl INTERFACE)

# Add our source files to the lib
target_sources(micropy_extmod_tweetnacl INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/tweetnacl.c
)

# Add the current directory as an include directory.
target_include_directories(micropy_extmod_tweetnacl INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
)

# Link our INTERFACE library to the usermod target.
target_link_libraries(tweetnacl INTERFACE micropy_extmod_tweetnacl)

#set(TWEETNACL_EXTMOD_DIR "${MICROPY_DIR}/extmod/tweetnacl")
#
#add_library(micropy_extmod_tweetnacl INTERFACE)
#
#target_include_directories(micropy_extmod_tweetnacl INTERFACE
#    ${MICROPY_DIR}/
#    ${MICROPY_PORT_DIR}/
#    ${TWEETNACL_EXTMOD_DIR}/
#)

